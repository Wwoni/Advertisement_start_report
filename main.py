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

def get_banner_id(href):
    """링크에서 ID 추출"""
    if not href: return "unknown"
    clean_path = href.split('?')[0]
    segments = clean_path.split('/')
    return segments[-1] if segments[-1] else segments[-2]

def create_side_by_side_pdf(web_img_path, app_img_path, output_pdf_path):
    """웹(왼쪽) + 앱(오른쪽) 나란히 배치하여 PDF 생성"""
    try:
        image1 = Image.open(web_img_path).convert('RGB')
        image2 = Image.open(app_img_path).convert('RGB')

        max_height = max(image1.height, image2.height)
        total_width = image1.width + image2.width
        
        new_image = Image.new('RGB', (total_width, max_height), (255, 255, 255))
        new_image.paste(image1, (0, 0))
        new_image.paste(image2, (image1.width, (max_height - image2.height) // 2))
        
        new_image.save(output_pdf_path)
        print(f"📄 PDF 병합 완료: {output_pdf_path}")
    except Exception as e:
        print(f"❌ PDF 생성 실패: {e}")

def handle_modal_if_exists(page):
    """
    접속 시 뜨는 '원티드 에이전트' 팝업(id="carousel")을 감지하고 닫습니다.
    """
    try:
        print("🕵️ 팝업(모달) 확인 중...")
        # 3초간 기다려봄 (제공해주신 HTML id="carousel" 사용)
        modal = page.locator("#carousel")
        
        if modal.is_visible(timeout=3000):
            print("❗️ 팝업 발견! 닫기를 시도합니다.")
            time.sleep(1)
            
            # 방법 1: 키보드 ESC 누르기 (가장 확실한 방법)
            page.keyboard.press("Escape")
            time.sleep(1)
            
            # 방법 2: ESC로 안 닫혔으면 닫기 버튼(X) 클릭 시도
            if modal.is_visible():
                # 'ab-close-button'은 보통 이런 마케팅 툴(Braze)의 닫기 버튼 클래스명
                # 또는 일반적인 닫기 버튼을 찾음
                close_btn = page.locator("button[class*='close'], button.ab-close-button").first
                if close_btn.is_visible():
                    close_btn.click()
                    print("👉 닫기 버튼(X) 클릭함")
                else:
                    # 닫기 버튼이 없으면 우측 상단 좌표 강제 클릭
                    print("👉 닫기 버튼을 찾지 못해 우측 상단 클릭 시도")
                    page.mouse.click(1800, 100) 
            
            time.sleep(2) # 닫히는 애니메이션 대기
            print("✅ 팝업 처리 완료")
        else:
            print("✅ 팝업이 없습니다. 바로 진행합니다.")
            
    except Exception as e:
        print(f"⚠️ 팝업 처리 중 특이사항 (무시하고 진행): {e}")

def main():
    client = WebClient(token=SLACK_TOKEN)

    with sync_playwright() as p:
        print("🚀 브라우저 실행 중...")
        browser = p.chromium.launch(headless=True)
        # 팝업이 잘 뜨도록 PC 환경의 User-Agent 설정
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()

        # ---------------------------------------------------------
        # [Step 0] 접속 및 팝업 제거
        # ---------------------------------------------------------
        print(f"🌐 접속 중: {TARGET_URL}")
        page.goto(TARGET_URL)
        
        # 여기서 팝업을 닫습니다!
        handle_modal_if_exists(page)

        # ---------------------------------------------------------
        # [Step 1] 메인 배너 찾기
        # ---------------------------------------------------------
        try:
            print("⏳ 메인 배너 로딩 대기...")
            # 팝업이 사라진 뒤 메인 배너가 보일 때까지 대기
            page.wait_for_selector("li[class*='BannerArea_MainBannerArea__slider__slide']", state="visible", timeout=10000)
            time.sleep(2)
        except:
            print("❌ 메인 배너를 찾을 수 없습니다. (스크린샷 저장)")
            page.screenshot(path="error_debug.png") # 디버깅용
            browser.close()
            return

        # 배너 요소들 찾기
        slides = page.locator("li[class*='BannerArea_MainBannerArea__slider__slide']")
        count = slides.count()
        print(f"📊 총 {count}개의 배너 식별됨.")
        
        if count == 0:
            print("❌ 배너 개수가 0입니다. 종료합니다.")
            browser.close()
            return

        # 배너 데이터 미리 수집
        banner_data = []
        for i in range(count):
            try:
                href = slides.nth(i).locator("a").get_attribute("href")
                banner_id = get_banner_id(href)
                banner_data.append({"index": i, "id": banner_id, "href": href})
            except:
                banner_data.append({"index": i, "id": f"unknown_{i}", "href": ""})

        # ---------------------------------------------------------
        # [Step 2] WEB 캡쳐 (PC View)
        # ---------------------------------------------------------
        print("\n📸 [Phase 1] WEB 캡쳐 시작")
        # 높이 1200으로 설정해 잘림 방지
        page.set_viewport_size({"width": 1920, "height": 1200})
        time.sleep(1)

        for i, item in enumerate(banner_data):
            # i > 0 이면 '다음' 버튼 클릭해서 넘기기
            if i > 0:
                try:
                    # 다음 버튼 클릭
                    next_btn = page.locator('button[aria-label="다음"]').first
                    next_btn.click()
                    time.sleep(1.5) 
                except Exception as e:
                    print(f"⚠️ 다음 버튼 클릭 실패: {e}")

            file_web = f"web_{i}.png"
            page.screenshot(path=file_web)
            print(f"  - Web [{i+1}/{count}] {item['id']} 캡쳐됨")

        # ---------------------------------------------------------
        # [Step 3] APP 캡쳐 (Mobile View)
        # ---------------------------------------------------------
        print("\n📸 [Phase 2] APP 캡쳐 시작")
        page.set_viewport_size({"width": 393, "height": 852}) 
        
        # 중요: 모바일로 바꾸고 새로고침해서 팝업이 또 뜰 수 있으므로 다시 처리
        page.reload()
        handle_modal_if_exists(page)
        
        try:
            page.wait_for_selector("li[class*='BannerArea_MainBannerArea__slider__slide']", state="visible", timeout=10000)
            time.sleep(2)
        except:
            pass 

        for i, item in enumerate(banner_data):
            file_app = f"app_{i}.png"
            try:
                # 해당 배너 찾아서 중앙으로 스크롤
                target_slide = page.locator(f"li[class*='BannerArea_MainBannerArea__slider__slide'] a[href='{item['href']}']").first
                target_slide.scroll_into_view_if_needed()
                time.sleep(0.5)
                
                page.screenshot(path=file_app)
                print(f"  - App [{i+1}/{count}] {item['id']} 캡쳐됨")
            except Exception as e:
                print(f"❌ App 캡쳐 실패 ({item['id']}): {e}")

        # ---------------------------------------------------------
        # [Step 4] 병합 및 전송
        # ---------------------------------------------------------
        print("\n📤 [Phase 3] 병합 및 슬랙 전송")
        
        for i, item in enumerate(banner_data):
            web_png = f"web_{i}.png"
            app_png = f"app_{i}.png"
            
            today = datetime.now().strftime("%y%m%d")
            pdf_filename = f"{today}_{item['id']}_게재보고.pdf"

            if os.path.exists(web_png) and os.path.exists(app_png):
                create_side_by_side_pdf(web_png, app_png, pdf_filename)
                
                if SLACK_TOKEN and SLACK_CHANNEL:
                    try:
                        client.files_upload_v2(
                            channel=SLACK_CHANNEL,
                            file=pdf_filename,
                            title=pdf_filename,
                            initial_comment=f"📢 [{i+1}/{count}] {item['id']} 배너 보고"
                        )
                        print(f"  ✅ 전송 완료: {item['id']}")
                    except Exception as e:
                        print(f"  ❌ 전송 실패: {e}")
                
                # 파일 삭제
                if os.path.exists(web_png): os.remove(web_png)
                if os.path.exists(app_png): os.remove(app_png)
                if os.path.exists(pdf_filename): os.remove(pdf_filename)
            else:
                print(f"⚠️ 이미지 누락: {item['id']}")

        print("\n✅ 모든 작업 완료!")
        browser.close()

if __name__ == "__main__":
    main()
