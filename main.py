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

# --- 캡쳐 사이즈 설정 (Pixel-Perfect) ---
WEB_WIDTH = 1100
WEB_HEIGHT = 728
APP_WIDTH = 353
APP_HEIGHT = 765
LAYOUT_GAP = 40

def get_banner_id(href):
    """링크에서 ID 추출"""
    if not href: return "unknown"
    clean_path = href.split('?')[0]
    segments = clean_path.split('/')
    return segments[-1] if segments[-1] else segments[-2]

def create_custom_layout_pdf(web_img_path, app_img_path, output_pdf_path):
    """[웹] [간격] [앱] 배치로 PDF 생성"""
    try:
        image1 = Image.open(web_img_path).convert('RGB')
        image2 = Image.open(app_img_path).convert('RGB')

        total_width = image1.width + image2.width + LAYOUT_GAP
        max_height = max(image1.height, image2.height)
        
        new_image = Image.new('RGB', (total_width, max_height), (255, 255, 255))
        new_image.paste(image1, (0, 0))
        new_image.paste(image2, (image1.width + LAYOUT_GAP, 0))
        
        new_image.save(output_pdf_path)
        print(f"📄 PDF 생성 완료: {output_pdf_path}")
    except Exception as e:
        print(f"❌ PDF 생성 실패: {e}")

def handle_popup(page):
    """팝업 감지 및 닫기 (Web/App 공통)"""
    try:
        # ESC 키 입력 (가장 빠르고 확실)
        page.keyboard.press("Escape")
        time.sleep(0.5)

        # 팝업 요소 확인 (id="carousel" 등)
        if page.locator("#carousel").is_visible():
            # 닫기 버튼 찾기 (여러가지 가능성 고려)
            close_btn = page.locator("button[class*='close'], button[aria-label*='Close'], button[aria-label*='닫기']").first
            if close_btn.is_visible():
                close_btn.click()
            else:
                # 닫기 버튼 없으면 좌표 클릭 (Dimmed 영역)
                page.mouse.click(10, 10)
            time.sleep(1)
    except Exception:
        pass

def main():
    client = WebClient(token=SLACK_TOKEN)

    with sync_playwright() as p:
        print("🚀 브라우저 실행 중...")
        browser = p.chromium.launch(headless=True)
        # 초기 컨텍스트: Web 사이즈
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": WEB_WIDTH, "height": WEB_HEIGHT}
        )
        page = context.new_page()

        # ---------------------------------------------------------
        # [Step 1] 전체 배너 리스트 파악 (Discovery Phase)
        # ---------------------------------------------------------
        print(f"🌐 리스트 확보를 위해 접속: {TARGET_URL}")
        page.goto(TARGET_URL)
        time.sleep(2)
        handle_popup(page)

        try:
            # 배너 슬라이드 요소들이 로딩될 때까지 대기
            page.wait_for_selector("li[class*='BannerArea_MainBannerArea__slider__slide']", state="visible", timeout=15000)
        except:
            print("❌ 배너 로딩 실패. 종료합니다.")
            browser.close()
            return

        # 전체 슬라이드 개수 및 정보 수집
        slides = page.locator("li[class*='BannerArea_MainBannerArea__slider__slide']")
        count = slides.count()
        print(f"📊 발견된 총 배너 수: {count}")

        # 목표 배너 리스트 만들기 (ID, HREF 저장)
        target_banners = []
        for i in range(count):
            try:
                href = slides.nth(i).locator("a").get_attribute("href")
                banner_id = get_banner_id(href)
                target_banners.append({"id": banner_id, "href": href})
            except:
                continue
        
        print(f"🎯 타겟팅할 배너 목록: {[b['id'] for b in target_banners]}")

        # ---------------------------------------------------------
        # [Step 2] 타겟 배너별 '새로고침' 낚시 (Capture Phase)
        # ---------------------------------------------------------
        for idx, target in enumerate(target_banners):
            print(f"\n--- [{idx+1}/{count}] 목표: {target['id']} 찾기 ---")
            
            found = False
            max_retries = 30 # 무한루프 방지용 (최대 30회 새로고침 시도)
            retry_count = 0

            while not found and retry_count < max_retries:
                # 1. 페이지 새로고침
                if retry_count > 0:
                    print(f"   🔄 새로고침 시도 ({retry_count}회)...")
                    page.reload()
                
                # 2. 로딩 대기 & 팝업 제거
                try:
                    page.wait_for_selector("li[class*='BannerArea_MainBannerArea__slider__slide']", state="visible", timeout=10000)
                except:
                    retry_count += 1
                    continue
                    
                handle_popup(page)
                
                # 3. 현재 첫 번째(0번 인덱스) 배너 확인
                try:
                    first_slide = page.locator("li[class*='BannerArea_MainBannerArea__slider__slide']").first
                    first_href = first_slide.locator("a").get_attribute("href")
                    current_id = get_banner_id(first_href)
                except:
                    current_id = "error"

                # 4. 타겟과 일치하는지 확인
                if current_id == target['id']:
                    print(f"   ✨ 발견! ({target['id']}가 첫 번째 자리에 옴)")
                    found = True
                    
                    # --- 캡쳐 프로세스 시작 ---
                    web_png = f"web_{idx}.png"
                    app_png = f"app_{idx}.png"
                    pdf_filename = f"{datetime.now().strftime('%y%m%d')}_{target['id']}_게재보고.pdf"

                    # (1) WEB 캡쳐 (현재 상태 그대로)
                    try:
                        page.set_viewport_size({"width": WEB_WIDTH, "height": WEB_HEIGHT})
                        handle_popup(page) # 해상도 변경 시 안전장치
                        time.sleep(0.5)
                        page.screenshot(path=web_png)
                        print("     📸 Web 캡쳐 완료")
                    except Exception as e:
                        print(f"     ❌ Web 캡쳐 에러: {e}")

                    # (2) APP 캡쳐 (뷰포트 변경 -> 캡쳐)
                    try:
                        page.set_viewport_size({"width": APP_WIDTH, "height": APP_HEIGHT})
                        time.sleep(1) # 레이아웃 변경 대기
                        handle_popup(page) # 모바일 팝업 다시 체크
                        page.screenshot(path=app_png)
                        print("     📸 App 캡쳐 완료")
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
                    
                    # 캡쳐 후엔 다음 타겟을 위해 브라우저 상태를 PC로 원복
                    page.set_viewport_size({"width": WEB_WIDTH, "height": WEB_HEIGHT})

                else:
                    # 일치하지 않으면 다음 시도
                    # print(f"   ...현재 {current_id} (목표: {target['id']}) -> 재시도")
                    retry_count += 1
            
            if not found:
                print(f"   ⚠️ {max_retries}회 시도했으나 {target['id']}가 첫 페이지에 뜨지 않아 건너뜁니다.")

        print("\n✅ 모든 작업 완료!")
        browser.close()

if __name__ == "__main__":
    main()
    
