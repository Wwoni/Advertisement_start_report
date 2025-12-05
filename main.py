import os
import time
from datetime import datetime
from playwright.sync_api import sync_playwright
from PIL import Image
from slack_sdk import WebClient

# --- 환경 변수 및 설정 ---
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL_ID")
TARGET_URL = "https://www.wanted.co.kr"

# --- [중요] 캡쳐 및 PDF 설정 ---
# [Web] 3개 노출을 위해 1920px(FHD)로 열고, 결과물은 1100px로 리사이징
WEB_VIEWPORT_W = 1920 
WEB_TARGET_WIDTH = 1100 
WEB_RENDER_HEIGHT = 2500 # 렌더링용 넉넉한 높이

# [App] 캡쳐는 390px(iPhone)로 하고, PDF에 넣을 때는 320px로 축소 (요청사항 반영)
APP_VIEWPORT_W = 390
APP_VIEWPORT_H = 844
APP_TARGET_WIDTH = 320 

LAYOUT_GAP = 30 # 웹과 앱 사이 간격 (좁게 조정)

def get_banner_id(href):
    if not href: return "unknown"
    clean_path = href.split('?')[0]
    segments = clean_path.split('/')
    return segments[-1] if segments[-1] else segments[-2]

def resize_image_high_quality(image_path, target_width):
    """이미지를 초고화질(LANCZOS)로 리사이징"""
    try:
        img = Image.open(image_path)
        w_percent = (target_width / float(img.size[0]))
        h_size = int((float(img.size[1]) * float(w_percent)))
        
        # 고품질 리사이징 필터 적용
        img = img.resize((target_width, h_size), Image.Resampling.LANCZOS)
        # 압축 없이 최고 화질로 저장
        img.save(image_path, quality=100, subsampling=0)
        return h_size
    except Exception as e:
        print(f"⚠️ 리사이징 실패: {e}")
        return 0

def create_custom_layout_pdf(web_img_path, app_img_path, output_pdf_path):
    """[웹(1100)] [간격(30)] [앱(320)] 배치로 PDF 생성"""
    try:
        image1 = Image.open(web_img_path).convert('RGB')
        image2 = Image.open(app_img_path).convert('RGB')

        max_height = max(image1.height, image2.height)
        total_width = image1.width + image2.width + LAYOUT_GAP
        
        # 흰색 배경 캔버스
        new_image = Image.new('RGB', (total_width, max_height), (255, 255, 255))
        
        # 웹(왼쪽 상단)
        new_image.paste(image1, (0, 0))
        # 앱(웹 바로 우측)
        new_image.paste(image2, (image1.width + LAYOUT_GAP, 0))
        
        # PDF 저장 (해상도 100.0 유지)
        new_image.save(output_pdf_path, "PDF", resolution=100.0, save_all=True)
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
    except Exception:
        pass

def get_dynamic_clip_height(page, min_height):
    """배너 리스트의 실제 바닥 좌표를 계산"""
    return page.evaluate(f"""() => {{
        const slider = document.querySelector("ul[class*='BannerArea_MainBannerArea__slider']");
        if (slider) {{
            const rect = slider.getBoundingClientRect();
            // 스크롤 위치 + 요소 바닥 + 여유분 50px
            return rect.bottom + window.scrollY + 50;
        }}
        return {min_height};
    }}""")

def main():
    client = WebClient(token=SLACK_TOKEN)

    with sync_playwright() as p:
        print("🚀 브라우저 실행 중 (초고화질 모드)...")
        browser = p.chromium.launch(headless=True)
        
        # [Web 컨텍스트] 3개 노출을 위해 1920px(FHD) 설정, 3배율(Retina)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": WEB_VIEWPORT_W, "height": WEB_RENDER_HEIGHT},
            device_scale_factor=3.0 
        )
        page = context.new_page()

        # 1. 접속 및 초기화
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
        print(f"📊 총 배너 수: {count}")

        target_banners = []
        for i in range(count):
            try:
                href = slides.nth(i).locator("a").get_attribute("href")
                banner_id = get_banner_id(href)
                target_banners.append({"id": banner_id, "href": href})
            except:
                pass
        
        print(f"🎯 목표 ID 목록: {[b['id'] for b in target_banners]}")

        # 2. 탐색 및 캡쳐 루프
        for idx, target in enumerate(target_banners):
            print(f"\n--- [{idx+1}/{count}] 목표: {target['id']} 찾는 중 ---")
            found = False
            
            # 매번 새로고침하여 초기 상태에서 시작 (가장 확실한 방법)
            page.reload()
            handle_popup(page)
            time.sleep(1)

            # [탐색 로직] 새로고침 후 '다음' 버튼을 누르며 찾기
            next_btn = page.locator('button[aria-label="다음"]').first
            target_locator = page.locator(f"li[class*='BannerArea_MainBannerArea__slider__slide'] a[href='{target['href']}']")
            
            # 최대 25번 이동 시도
            for c in range(25):
                # 1. 현재 화면에 타겟이 보이는지 확인 (좌측 영역)
                if target_locator.count() > 0:
                    box = target_locator.first.bounding_box()
                    # 1920px 기준 좌측 500px 이내에 있으면 첫번째 슬라이드로 간주
                    if box and 0 <= box['x'] < 500:
                        print(f"   ✨ 발견! ({c}번 이동)")
                        found = True
                        break
                
                # 2. 없으면 다음 버튼 클릭
                if next_btn.is_visible() and not next_btn.is_disabled():
                    try:
                        next_btn.click(timeout=1000) # 타임아웃 짧게 설정
                        time.sleep(1.0) # 슬라이드 애니메이션 대기
                    except Exception:
                        print("   ⛔ 클릭 실패 (버튼 비활성화 가능성)")
                        break
                else:
                    print("   ⛔ 더 이상 이동 불가 (마지막)")
                    break

            # 3. 캡쳐 및 전송
            if found:
                web_png = f"web_{idx}.png"
                app_png = f"app_{idx}.png"
                pdf_filename = f"{datetime.now().strftime('%y%m%d')}_{target['id']}_게재보고.pdf"

                # (1) WEB 캡쳐 (1920px 캡쳐 -> 1100px 리사이징)
                try:
                    # 뷰포트 확실하게 설정
                    page.set_viewport_size({"width": WEB_VIEWPORT_W, "height": WEB_RENDER_HEIGHT})
                    time.sleep(0.5)
                    handle_popup(page)
                    
                    clip_height = get_dynamic_clip_height(page, 800)
                    
                    # 1920px 전체 너비로 찍음 (3개 배너 모두 포함)
                    page.screenshot(path=web_png, clip={"x": 0, "y": 0, "width": WEB_VIEWPORT_W, "height": clip_height})
                    
                    # 1100px로 고화질 리사이징
                    resize_image_high_quality(web_png, WEB_TARGET_WIDTH)
                    print(f"     📸 Web 캡쳐 완료 (3개 노출 보장)")
                except Exception as e:
                    print(f"     ❌ Web 캡쳐 에러: {e}")

                # (2) APP 캡쳐 (320px 리사이징)
                try:
                    # 모바일 뷰포트 설정
                    page.set_viewport_size({"width": APP_VIEWPORT_W, "height": APP_VIEWPORT_H})
                    time.sleep(1)
                    handle_popup(page)
                    
                    mobile_clip_height = get_dynamic_clip_height(page, 765)
                    page.set_viewport_size({"width": APP_VIEWPORT_W, "height": int(mobile_clip_height + 100)})
                    
                    page.screenshot(path=app_png, clip={"x": 0, "y": 0, "width": APP_VIEWPORT_W, "height": mobile_clip_height})
                    
                    # 요청하신 대로 작게 리사이징 (320px)
                    resize_image_high_quality(app_png, APP_TARGET_WIDTH)
                    print(f"     📸 App 캡쳐 완료 (사이즈 축소)")
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
                
                # 다음 타겟을 위해 Web 사이즈 복구
                page.set_viewport_size({"width": WEB_VIEWPORT_W, "height": WEB_RENDER_HEIGHT})
            else:
                print(f"   ❌ {target['id']} 미발견 (Skip)")

        print("\n✅ 모든 작업 완료!")
        browser.close()

if __name__ == "__main__":
    main()
