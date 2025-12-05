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

# --- [초고화질 설정] ---
# Web: 3개 노출을 위해 1920px 뷰포트 사용 (3배율 시 5760px)
WEB_VIEWPORT_W = 1920
WEB_RENDER_HEIGHT = 2500
WEB_TARGET_WIDTH = 1100 # 결과물 리사이징 너비

# App: 모바일 뷰포트
APP_VIEWPORT_W = 400 
APP_VIEWPORT_H = 1000
APP_TARGET_WIDTH = 320 # 결과물 리사이징 너비

LAYOUT_GAP = 20 

def get_banner_id(href):
    if not href: return "unknown"
    clean_path = href.split('?')[0]
    segments = clean_path.split('/')
    return segments[-1] if segments[-1] else segments[-2]

def resize_image_high_quality(image_path, target_width):
    """깨짐 없는 초고화질 리사이징"""
    try:
        img = Image.open(image_path)
        w_percent = (target_width / float(img.size[0]))
        h_size = int((float(img.size[1]) * float(w_percent)))
        
        img = img.resize((target_width, h_size), Image.Resampling.LANCZOS)
        img.save(image_path, quality=100, subsampling=0)
        return h_size
    except Exception as e:
        print(f"⚠️ 리사이징 실패: {e}")
        return 0

def create_custom_layout_pdf(web_img_path, app_img_path, output_pdf_path):
    """[웹] [앱] 좌측 정렬 배치"""
    try:
        image1 = Image.open(web_img_path).convert('RGB')
        image2 = Image.open(app_img_path).convert('RGB')

        max_height = max(image1.height, image2.height)
        total_width = image1.width + image2.width + LAYOUT_GAP
        
        new_image = Image.new('RGB', (total_width, max_height), (255, 255, 255))
        new_image.paste(image1, (0, 0))
        new_image.paste(image2, (image1.width + LAYOUT_GAP, 0))
        
        # 300 DPI 고해상도 PDF 저장
        new_image.save(output_pdf_path, "PDF", resolution=300.0, save_all=True)
        print(f"📄 PDF 생성 완료: {output_pdf_path}")
    except Exception as e:
        print(f"❌ PDF 생성 실패: {e}")

def handle_popup(page):
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
        if page.locator("div[class*='Modal']").is_visible() or page.locator("#carousel").is_visible():
            close_btn = page.locator("button[class*='close'], button[aria-label*='Close'], button[aria-label*='닫기']").first
            if close_btn.is_visible():
                close_btn.click()
            else:
                page.mouse.click(10, 10)
            time.sleep(1)
    except:
        pass

def get_dynamic_clip_height(page, selector, min_height):
    return page.evaluate(f"""() => {{
        const el = document.querySelector("{selector}");
        if (el) {{
            const rect = el.getBoundingClientRect();
            return rect.bottom + window.scrollY + 60; 
        }}
        return {min_height};
    }}""")

def main():
    client = WebClient(token=SLACK_TOKEN)

    with sync_playwright() as p:
        print("🚀 브라우저 실행 (3배율 Retina)...")
        browser = p.chromium.launch(headless=True)
        
        # [Web 컨텍스트] 1920px (3열 보장) + 3배율
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": WEB_VIEWPORT_W, "height": WEB_RENDER_HEIGHT},
            device_scale_factor=3.0
        )
        page = context.new_page()

        # 1. 초기 접속 및 리스트 파악
        print(f"🌐 접속 중: {TARGET_URL}")
        page.goto(TARGET_URL)
        time.sleep(3)
        handle_popup(page)

        try:
            page.wait_for_selector("li[class*='BannerArea_MainBannerArea__slider__slide']", state="visible", timeout=15000)
        except:
            print("❌ 배너 로딩 실패")
            browser.close()
            return

        slides = page.locator("li[class*='BannerArea_MainBannerArea__slider__slide']")
        count = slides.count()
        print(f"📊 총 {count}개의 배너 발견")

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
        # [Step 2] 배너별 시각적 위치 추적 (Visual Targeting)
        # ---------------------------------------------------------
        for idx, target in enumerate(target_banners):
            print(f"\n--- [{idx+1}/{count}] 목표: {target['id']} 위치 찾는 중 ---")
            found = False
            
            # 1. 매번 새로고침하여 초기 상태(Preload 배너가 0번에 있는 상태)로 만듦
            page.reload()
            handle_popup(page)
            time.sleep(1) # 로딩 안정화

            next_btn = page.locator('button[aria-label="다음"]').first
            target_locator = page.locator(f"li[class*='BannerArea_MainBannerArea__slider__slide'] a[href='{target['href']}']")

            # 2. '다음' 버튼을 누르며 목표 배너가 "화면 맨 왼쪽"에 올 때까지 이동
            # (Preload 배너는 0번, Lazy 배너는 N번 눌러야 옴)
            for c in range(30):
                # (A) 타겟이 현재 화면 좌측(0~300px) 구간에 있는지 확인
                if target_locator.count() > 0:
                    box = target_locator.first.bounding_box()
                    # 1920px 기준, 좌측 500px 이내면 '첫 번째' 슬롯으로 간주
                    if box and 0 <= box['x'] < 500:
                        print(f"   ✨ 발견! ({c}회 클릭하여 첫 번째 자리 확보)")
                        found = True
                        break
                
                # (B) 아니면 '다음' 클릭하여 슬라이드 넘김
                if next_btn.is_visible() and not next_btn.is_disabled():
                    try:
                        next_btn.click()
                        time.sleep(0.8) # 슬라이드 이동 시간 대기
                    except:
                        break
                else:
                    # 더 이상 넘길 곳이 없는데 못 찾음
                    break

            # -----------------------------------------------------
            # [Step 3] 캡쳐 및 전송
            # -----------------------------------------------------
            if found:
                web_png = f"web_{idx}.png"
                app_png = f"app_{idx}.png"
                pdf_filename = f"{datetime.now().strftime('%y%m%d')}_{target['id']}_게재보고.pdf"

                # (1) WEB 캡쳐: 1920px로 찍고 -> 1100px 리사이징
                try:
                    # 높이는 자동 계산
                    clip_height = get_dynamic_clip_height(page, "ul[class*='BannerArea_MainBannerArea__slider']", 800)
                    
                    page.screenshot(path=web_png, clip={"x": 0, "y": 0, "width": WEB_VIEWPORT_W, "height": clip_height})
                    resize_image_high_quality(web_png, WEB_TARGET_WIDTH)
                    print(f"     📸 Web 캡쳐 완료")
                except Exception as e:
                    print(f"     ❌ Web 캡쳐 에러: {e}")

                # (2) APP 캡쳐: 모바일 뷰로 변경 후 해당 배너 찍기
                try:
                    # 모바일 뷰포트 설정
                    page.set_viewport_size({"width": APP_VIEWPORT_W, "height": APP_VIEWPORT_H})
                    time.sleep(1)
                    handle_popup(page) # 모바일 팝업 제거
                    
                    # 모바일에서는 해당 배너가 보이게 스크롤
                    target_slide = page.locator(f"li[class*='BannerArea_MainBannerArea__slider__slide'] a[href='{target['href']}']").first
                    target_slide.scroll_into_view_if_needed()
                    time.sleep(0.5)
                    
                    # 높이 자동 계산 + 캡쳐
                    m_clip_height = get_dynamic_clip_height(page, "ul[class*='BannerArea_MainBannerArea__slider']", 765)
                    # 캡쳐를 위해 잠시 뷰포트 늘림
                    page.set_viewport_size({"width": APP_VIEWPORT_W, "height": int(m_clip_height + 100)})
                    
                    page.screenshot(path=app_png, clip={"x": 0, "y": 0, "width": APP_VIEWPORT_W, "height": m_clip_height})
                    resize_image_high_quality(app_png, APP_TARGET_WIDTH)
                    print(f"     📸 App 캡쳐 완료")
                except Exception as e:
                    print(f"     ❌ App 캡쳐 에러: {e}")

                # (3) PDF 생성 & 전송
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
                
                # 다음 타겟을 위해 Web 사이즈 복구 (중요)
                page.set_viewport_size({"width": WEB_VIEWPORT_W, "height": WEB_RENDER_HEIGHT})
            else:
                print(f"   ❌ {target['id']} 추적 실패 (건너뜀)")

        print("\n✅ 모든 작업 완료!")
        browser.close()

if __name__ == "__main__":
    main()
    
