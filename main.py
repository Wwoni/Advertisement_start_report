import os
import time
import random
from datetime import datetime
from playwright.sync_api import sync_playwright
from PIL import Image
from slack_sdk import WebClient

# --- 환경 변수 및 설정 ---
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL_ID")
TARGET_URL = "https://www.wanted.co.kr"

# --- 캡쳐 사이즈 설정 ---
# [Web] 렌더링은 크게(1500), 캡쳐는 지정 사이즈(1100x728)로 오려냄 (CSS 픽셀 기준)
WEB_VIEWPORT_W, WEB_VIEWPORT_H = 1100, 1500 
WEB_CAPTURE_W, WEB_CAPTURE_H = 1100, 728

# [App] 모바일 뷰포트 (CSS 픽셀 기준)
APP_WIDTH, APP_HEIGHT = 353, 765

LAYOUT_GAP = 40 # PDF 좌우 간격

def get_banner_id(href):
    """링크에서 ID 추출"""
    if not href: return "unknown"
    clean_path = href.split('?')[0]
    segments = clean_path.split('/')
    return segments[-1] if segments[-1] else segments[-2]

def create_custom_layout_pdf(web_img_path, app_img_path, output_pdf_path):
    """[웹] [간격] [앱] 배치로 고화질 PDF 생성"""
    try:
        image1 = Image.open(web_img_path).convert('RGB')
        image2 = Image.open(app_img_path).convert('RGB')

        # 이미지 사이즈가 2배로 커졌으므로 캔버스도 그에 맞춰 생성
        total_width = image1.width + image2.width + LAYOUT_GAP
        max_height = max(image1.height, image2.height)
        
        new_image = Image.new('RGB', (total_width, max_height), (255, 255, 255))
        new_image.paste(image1, (0, 0))
        new_image.paste(image2, (image1.width + LAYOUT_GAP, 0))
        
        # PDF 저장 시 해상도 유지
        new_image.save(output_pdf_path, "PDF", resolution=100.0, save_all=True)
        print(f"📄 PDF 생성 완료: {output_pdf_path}")
    except Exception as e:
        print(f"❌ PDF 생성 실패: {e}")

def handle_popup(page):
    """팝업 감지 및 닫기"""
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
        if page.locator("#carousel").is_visible():
            close_btn = page.locator("button[class*='close'], button[aria-label*='Close'], button[aria-label*='닫기']").first
            if close_btn.is_visible():
                close_btn.click()
            else:
                page.mouse.click(10, 10)
            time.sleep(1)
    except Exception:
        pass

def main():
    client = WebClient(token=SLACK_TOKEN)

    with sync_playwright() as p:
        print("🚀 브라우저 실행 중 (고화질 모드)...")
        browser = p.chromium.launch(headless=True)
        
        # [중요] device_scale_factor=2 추가 (레티나급 고화질 설정)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": WEB_VIEWPORT_W, "height": WEB_VIEWPORT_H},
            device_scale_factor=2 
        )
        page = context.new_page()

        # ---------------------------------------------------------
        # [Step 1] 전체 배너 리스트 파악
        # ---------------------------------------------------------
        print(f"🌐 접속 중: {TARGET_URL}")
        page.goto(TARGET_URL)
        time.sleep(2)
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

        # 타겟 리스트 확보
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
        # [Step 2] 배너별 하이브리드 탐색
        # ---------------------------------------------------------
        for idx, target in enumerate(target_banners):
            print(f"\n--- [{idx+1}/{count}] 목표: {target['id']} 찾는 중 ---")
            
            found = False
            
            # (A) 전략 1: 새로고침 시도 (Preload/Eager용) - 최대 10회
            refresh_limit = 10
            for r in range(refresh_limit):
                if r > 0: 
                    page.reload()
                    handle_popup(page)
                    try:
                        page.wait_for_selector("li[class*='BannerArea_MainBannerArea__slider__slide']", state="visible", timeout=5000)
                    except:
                        continue

                try:
                    # 첫 번째 슬라이드가 타겟인지 확인
                    first_slide = page.locator("li[class*='BannerArea_MainBannerArea__slider__slide']").first
                    first_href = first_slide.locator("a").get_attribute("href")
                    
                    if target['href'] in first_href:
                        print(f"   ✨ [새로고침] {r+1}회 만에 첫 번째 자리에서 발견!")
                        found = True
                        break
                except:
                    pass
            
            # (B) 전략 2: 페이지네이션 탐색 (Lazy용)
            if not found:
                print(f"   ⚠️ 새로고침으로 못 찾음 -> [페이지네이션] 탐색 시작")
                
                target_locator = page.locator(f"li[class*='BannerArea_MainBannerArea__slider__slide'] a[href='{target['href']}']")
                next_btn = page.locator('button[aria-label="다음"]').first
                
                max_clicks = 20
                for c in range(max_clicks):
                    if target_locator.is_visible():
                        print(f"   ✨ [페이지네이션] {c}번 이동 후 발견!")
                        found = True
                        break
                    
                    if next_btn.is_visible():
                        next_btn.click()
                        time.sleep(1) # 애니메이션 대기
                    else:
                        break

            # -----------------------------------------------------
            # [Step 3] 캡쳐 및 전송 (발견 시)
            # -----------------------------------------------------
            if found:
                web_png = f"web_{idx}.png"
                app_png = f"app_{idx}.png"
                pdf_filename = f"{datetime.now().strftime('%y%m%d')}_{target['id']}_게재보고.pdf"

                # (1) WEB 캡쳐 (Clip 사용)
                try:
                    # 렌더링은 1500px 높이로, 캡쳐는 728px만 오려냄
                    page.set_viewport_size({"width": WEB_VIEWPORT_W, "height": WEB_VIEWPORT_H})
                    time.sleep(0.5)
                    handle_popup(page) 
                    
                    # clip 옵션 사용 시 device_scale_factor가 자동 적용되어 고화질로 저장됨
                    page.screenshot(path=web_png, clip={"x": 0, "y": 0, "width": WEB_CAPTURE_W, "height": WEB_CAPTURE_H})
                    print("     📸 Web 캡쳐 완료 (High Quality)")
                except Exception as e:
                    print(f"     ❌ Web 캡쳐 에러: {e}")

                # (2) APP 캡쳐 (Viewport 변경)
                try:
                    page.set_viewport_size({"width": APP_WIDTH, "height": APP_HEIGHT})
                    time.sleep(1) 
                    handle_popup(page)
                    page.screenshot(path=app_png)
                    print("     📸 App 캡쳐 완료 (High Quality)")
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
                    
                    # 파일 정리
                    for f in [web_png, app_png, pdf_filename]:
                        if os.path.exists(f): os.remove(f)
                
                # 다음 타겟을 위해 Web 사이즈 복구
                page.set_viewport_size({"width": WEB_VIEWPORT_W, "height": WEB_VIEWPORT_H})
            else:
                print(f"   ❌ 결국 {target['id']}를 찾지 못했습니다. (건너뜁니다)")

        print("\n✅ 모든 작업 완료!")
        browser.close()

if __name__ == "__main__":
    main() 
