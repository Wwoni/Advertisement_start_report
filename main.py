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
        
        # 흰색 배경 캔버스 생성
        new_image = Image.new('RGB', (total_width, max_height), (255, 255, 255))
        
        # 붙여넣기 (좌측: Web, 우측: App)
        new_image.paste(image1, (0, 0))
        # 앱 이미지는 세로 중앙 정렬
        new_image.paste(image2, (image1.width, (max_height - image2.height) // 2))
        
        new_image.save(output_pdf_path)
        print(f"📄 PDF 병합 완료: {output_pdf_path}")
    except Exception as e:
        print(f"❌ PDF 생성 실패: {e}")

def handle_popup(page):
    """
    id="carousel" 또는 일반적인 닫기 버튼을 가진 팝업을 처리합니다.
    """
    try:
        # 1. ESC 키 누르기 (가장 빠름)
        page.keyboard.press("Escape")
        time.sleep(0.5)

        # 2. 특정 팝업(carousel)이 여전히 보이면 닫기 시도
        if page.locator("#carousel").is_visible():
            print("🕵️ 'carousel' 팝업 감지됨. 닫기 시도...")
            # 닫기 버튼 찾기 (일반적인 클래스명 또는 aria-label)
            close_btn = page.locator("button[class*='close'], button[aria-label*='Close'], button[aria-label*='닫기']").first
            if close_btn.is_visible():
                close_btn.click()
                print("👉 닫기 버튼 클릭됨")
            else:
                # 닫기 버튼이 명시적으로 없으면 화면 빈 곳 클릭 (Dimmed 영역)
                page.mouse.click(10, 10)
            time.sleep(1)
    except Exception as e:
        print(f"⚠️ 팝업 처리 중 경고: {e}")

def main():
    client = WebClient(token=SLACK_TOKEN)

    with sync_playwright() as p:
        print("🚀 브라우저 실행 중...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()

        # 1. 접속
        print(f"🌐 접속 중: {TARGET_URL}")
        page.goto(TARGET_URL)
        time.sleep(3) # 초기 로딩 대기

        # 2. 초기 팝업 제거
        handle_popup(page)

        # 3. 배너 리스트 파악
        try:
            page.wait_for_selector("li[class*='BannerArea_MainBannerArea__slider__slide']", state="visible", timeout=15000)
        except:
            print("❌ 메인 배너를 찾지 못했습니다.")
            browser.close()
            return

        slides = page.locator("li[class*='BannerArea_MainBannerArea__slider__slide']")
        count = slides.count()
        print(f"📊 총 {count}개의 배너를 발견했습니다.")

        # 배너 데이터 수집
        banner_data = []
        for i in range(count):
            try:
                href = slides.nth(i).locator("a").get_attribute("href")
                banner_id = get_banner_id(href)
                banner_data.append({"id": banner_id, "href": href})
            except:
                banner_data.append({"id": f"unknown_{i}", "href": ""})

        # 4. 반복 작업 시작 (Web 캡쳐 -> App 캡쳐 -> Web 복귀 후 다음 버튼)
        for i, item in enumerate(banner_data):
            print(f"\n--- [{i+1}/{count}] {item['id']} 작업 시작 ---")
            
            web_png = f"web_{i}.png"
            app_png = f"app_{i}.png"
            pdf_filename = f"{datetime.now().strftime('%y%m%d')}_{item['id']}_게재보고.pdf"

            # [Phase 1] WEB 캡쳐 (PC)
            try:
                page.set_viewport_size({"width": 1920, "height": 1200}) # 높이 여유있게
                # 첫 번째 루프가 아니면 팝업 체크는 생략 가능하나, 혹시 모르니 ESC 한번
                if i == 0: handle_popup(page) 
                time.sleep(0.5)
                page.screenshot(path=web_png)
                print("📸 Web 캡쳐 완료")
            except Exception as e:
                print(f"❌ Web 캡쳐 에러: {e}")

            # [Phase 2] APP 캡쳐 (Mobile) - 이전 방식으로 복원
            try:
                page.set_viewport_size({"width": 393, "height": 852})
                time.sleep(1) # 레이아웃 변경 대기
                
                # 모바일 뷰로 바뀌면서 팝업이 다시 뜰 수 있으므로 닫기 시도 [중요!]
                handle_popup(page)
                
                page.screenshot(path=app_png)
                print("📸 App 캡쳐 완료")
            except Exception as e:
                print(f"❌ App 캡쳐 에러: {e}")

            # [Phase 3] PDF 병합 및 전송
            if os.path.exists(web_png) and os.path.exists(app_png):
                create_side_by_side_pdf(web_png, app_png, pdf_filename)
                
                if SLACK_TOKEN and SLACK_CHANNEL:
                    try:
                        client.files_upload_v2(
                            channel=SLACK_CHANNEL,
                            file=pdf_filename,
                            title=pdf_filename,
                            initial_comment=f"📢 [{i+1}/{count}] {item['id']} 배너 게재 보고"
                        )
                        print(f"✅ 슬랙 전송 완료")
                    except Exception as e:
                        print(f"❌ 슬랙 전송 실패: {e}")
                
                # 파일 정리
                for f in [web_png, app_png, pdf_filename]:
                    if os.path.exists(f): os.remove(f)

            # [Phase 4] 다음 배너 준비 (Web으로 복귀 후 '다음' 클릭)
            try:
                page.set_viewport_size({"width": 1920, "height": 1200})
                time.sleep(0.5)
                
                # '다음' 버튼 클릭
                next_btn = page.locator('button[aria-label="다음"]').first
                if next_btn.is_visible():
                    next_btn.click()
                    time.sleep(1.5) # 슬라이드 애니메이션 대기
                else:
                    print("⚠️ '다음' 버튼을 찾을 수 없습니다.")
            except Exception as e:
                print(f"⚠️ 다음 버튼 클릭 중 오류: {e}")

        print("\n✅ 모든 작업 완료!")
        browser.close()

if __name__ == "__main__":
    main()
    
