import os
import time
from datetime import datetime
from playwright.sync_api import sync_playwright
from PIL import Image
from slack_sdk import WebClient

# --- 환경 변수 ---
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL_ID")
TARGET_URL = "https://www.wanted.co.kr"

# --- [고화질 & 레이아웃 설정] ---
# Web: 1920px(FHD)로 찍어서 3개 노출 보장 -> 1100px로 축소
WEB_VIEWPORT_W = 1920
WEB_TARGET_WIDTH = 1100
WEB_RENDER_HEIGHT = 2000

# App: 450px(넉넉한 모바일)로 찍고 -> 320px로 축소 (요청하신 컴팩트 사이즈)
APP_VIEWPORT_W = 450 
APP_TARGET_WIDTH = 320
APP_VIEWPORT_H = 900

LAYOUT_GAP = 20 # 간격을 더 좁혀서 좌측 정렬 느낌 강화

def get_banner_id(href):
    if not href: return "unknown"
    clean_path = href.split('?')[0]
    segments = clean_path.split('/')
    return segments[-1] if segments[-1] else segments[-2]

def resize_image_high_quality(image_path, target_width):
    """LANCZOS 필터로 깨짐 없이 선명하게 리사이징"""
    try:
        img = Image.open(image_path)
        w_percent = (target_width / float(img.size[0]))
        h_size = int((float(img.size[1]) * float(w_percent)))
        
        img = img.resize((target_width, h_size), Image.Resampling.LANCZOS)
        # 품질 100, 서브샘플링 0 (최고 화질 설정)
        img.save(image_path, quality=100, subsampling=0)
        return h_size
    except Exception as e:
        print(f"⚠️ 리사이징 실패: {e}")
        return 0

def create_custom_layout_pdf(web_img_path, app_img_path, output_pdf_path):
    """[웹] [간격] [앱] 좌측 정렬 배치"""
    try:
        image1 = Image.open(web_img_path).convert('RGB')
        image2 = Image.open(app_img_path).convert('RGB')

        max_height = max(image1.height, image2.height)
        # 전체 캔버스 너비
        total_width = image1.width + image2.width + LAYOUT_GAP
        
        new_image = Image.new('RGB', (total_width, max_height), (255, 255, 255))
        
        # 웹 (0,0)
        new_image.paste(image1, (0, 0))
        # 앱 (웹 끝나는 지점 + 간격)
        new_image.paste(image2, (image1.width + LAYOUT_GAP, 0))
        
        new_image.save(output_pdf_path, "PDF", resolution=100.0, save_all=True)
        print(f"📄 PDF 생성 완료: {output_pdf_path}")
    except Exception as e:
        print(f"❌ PDF 생성 실패: {e}")

def handle_popup(page):
    """집요하게 팝업 닫기"""
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
        # 캐러셀 팝업, 일반 모달, 마케팅 배너 등
        popups = page.locator("#carousel, div[class*='Modal'], div[class*='Popup']")
        if popups.first.is_visible():
            close_btn = page.locator("button[class*='close'], button[aria-label*='Close'], button[aria-label*='닫기']").first
            if close_btn.is_visible():
                close_btn.click()
            else:
                page.mouse.click(10, 10) # 딤드 영역 클릭
            time.sleep(1)
    except:
        pass

def get_dynamic_clip_height(page, selector, min_height):
    """선택한 요소의 바닥까지 높이 계산"""
    return page.evaluate(f"""() => {{
        const el = document.querySelector("{selector}");
        if (el) {{
            const rect = el.getBoundingClientRect();
            return rect.bottom + window.scrollY + 50; 
        }}
        return {min_height};
    }}""")

def main():
    client = WebClient(token=SLACK_TOKEN)

    with sync_playwright() as p:
        print("🚀 브라우저 실행 (Ultra High Quality)...")
        browser = p.chromium.launch(headless=True)
        
        # [Web 컨텍스트] 3배율(Retina급) 고화질 + 1920px(3개 노출 보장)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": WEB_VIEWPORT_W, "height": WEB_RENDER_HEIGHT},
            device_scale_factor=3.0
        )
        page = context.new_page()

        # 1. 초기 접속 (딱 한 번만)
        print(f"🌐 접속 중: {TARGET_URL}")
        page.goto(TARGET_URL)
        time.sleep(3)
        handle_popup(page)

        # 배너 섹션 로딩 대기
        try:
            page.wait_for_selector("li[class*='BannerArea_MainBannerArea__slider__slide']", state="visible", timeout=15000)
        except:
            print("❌ 배너 로딩 실패")
            browser.close()
            return

        slides = page.locator("li[class*='BannerArea_MainBannerArea__slider__slide']")
        count = slides.count()
        print(f"📊 총 {count}개의 배너를 발견했습니다.")

        # 타겟 리스트 생성
        target_banners = []
        for i in range(count):
            try:
                href = slides.nth(i).locator("a").get_attribute("href")
                banner_id = get_banner_id(href)
                target_banners.append({"id": banner_id, "href": href})
            except:
                pass
        
        print(f"🎯 목표 ID 목록: {[b['id'] for b in target_banners]}")

        # ---------------------------------------------------------
        # [Step 2] 순차 주행 (새로고침 없이 끝까지 간다)
        # ---------------------------------------------------------
        next_btn = page.locator('button[aria-label="다음"]').first
        
        for idx, target in enumerate(target_banners):
            print(f"\n--- [{idx+1}/{count}] 목표: {target['id']} 추적 중 ---")
            found = False
            
            # (A) 목표 배너가 나올 때까지 '다음' 버튼 클릭 (최대 30회)
            # *새로고침 절대 금지* - 현재 상태에서 계속 진행
            for c in range(30):
                # 1. 현재 화면의 첫 번째 슬라이드가 목표인지 확인
                try:
                    # 현재 뷰포트에 보이는 첫 번째 슬라이드 식별
                    # (slick-active 클래스나 1920px 기준 좌측 좌표로 식별)
                    target_locator = page.locator(f"li[class*='BannerArea_MainBannerArea__slider__slide'] a[href='{target['href']}']")
                    
                    if target_locator.count() > 0:
                        box = target_locator.first.bounding_box()
                        # 화면 왼쪽(0~500px) 구간에 들어와 있으면 "주인공"으로 인정
                        if box and 0 <= box['x'] < 500:
                            print(f"   ✨ 발견! ({c}칸 이동함)")
                            found = True
                            break
                except:
                    pass

                # 2. 아니면 '다음' 버튼 클릭
                if next_btn.is_visible():
                    # 버튼이 비활성화(disabled) 상태면 더 갈 곳이 없으므로 중단
                    if next_btn.get_attribute("disabled") is not None:
                        print("   ⛔ 마지막 슬라이드 도달. 이동 불가.")
                        break
                    
                    next_btn.click()
                    time.sleep(1.0) # 애니메이션 대기
                else:
                    break
            
            # (B) 캡쳐 및 전송
            if found:
                web_png = f"web_{idx}.png"
                app_png = f"app_{idx}.png"
                pdf_filename = f"{datetime.now().strftime('%y%m%d')}_{target['id']}_게재보고.pdf"

                # [Web 캡쳐] 1920px (3개 보임) -> 1100px 리사이징
                try:
                    page.set_viewport_size({"width": WEB_VIEWPORT_W, "height": WEB_RENDER_HEIGHT})
                    time.sleep(0.5)
                    handle_popup(page)
                    
                    # 배너 바닥 좌표 자동 계산 (ul 태그 기준)
                    clip_height = get_dynamic_clip_height(page, "ul[class*='BannerArea_MainBannerArea__slider']", 800)
                    
                    page.screenshot(path=web_png, clip={"x": 0, "y": 0, "width": WEB_VIEWPORT_W, "height": clip_height})
                    resize_image_high_quality(web_png, WEB_TARGET_WIDTH) # 1100px로 축소
                    print(f"     📸 Web 캡쳐 완료 (선명함+3개노출)")
                except Exception as e:
                    print(f"     ❌ Web 캡쳐 오류: {e}")

                # [App 캡쳐] 450px -> 320px 리사이징 (스크롤 이동)
                try:
                    page.set_viewport_size({"width": APP_VIEWPORT_W, "height": APP_VIEWPORT_H})
                    time.sleep(1)
                    handle_popup(page) # 모바일 팝업 닫기
                    
                    # 모바일에서는 해당 배너로 스크롤 이동
                    target_slide = page.locator(f"li[class*='BannerArea_MainBannerArea__slider__slide'] a[href='{target['href']}']").first
                    target_slide.scroll_into_view_if_needed()
                    time.sleep(0.5)
                    
                    # 모바일 높이 자동 계산
                    m_clip_height = get_dynamic_clip_height(page, "ul[class*='BannerArea_MainBannerArea__slider']", 765)
                    # 캡쳐를 위해 잠시 뷰포트 높이 늘림
                    page.set_viewport_size({"width": APP_VIEWPORT_W, "height": int(m_clip_height + 100)})
                    
                    page.screenshot(path=app_png, clip={"x": 0, "y": 0, "width": APP_VIEWPORT_W, "height": m_clip_height})
                    resize_image_high_quality(app_png, APP_TARGET_WIDTH) # 320px로 축소
                    print(f"     📸 App 캡쳐 완료 (선명함+320px)")
                except Exception as e:
                    print(f"     ❌ App 캡쳐 오류: {e}")

                # [PDF 생성 & 전송]
                if os.path.exists(web_png) and os.path.exists(app_png):
                    create_custom_layout_pdf(web_png, app_png, pdf_filename)
                    
                    if SLACK_TOKEN and SLACK_CHANNEL:
                        try:
                            client.files_upload_v2(
                                channel=SLACK_CHANNEL,
                                file=pdf_filename,
                                title=pdf_filename,
                                initial_comment=f"📢 [{idx+1}/{count}] {target['id']} 게재 보고"
                            )
                            print("     ✅ 슬랙 전송 완료")
                        except Exception as e:
                            print(f"     ❌ 슬랙 전송 실패: {e}")
                    
                    for f in [web_png, app_png, pdf_filename]:
                        if os.path.exists(f): os.remove(f)
                
                # 다음 타겟을 위해 Web 상태로 복구 (중요: 위치는 유지됨)
                page.set_viewport_size({"width": WEB_VIEWPORT_W, "height": WEB_RENDER_HEIGHT})
            else:
                print(f"   ❌ {target['id']} 결국 못 찾음 (Skip)")

        print("\n✅ 모든 작업 완료!")
        browser.close()

if __name__ == "__main__":
    main()
