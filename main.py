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
    """
    웹(왼쪽) + 앱(오른쪽) 나란히 배치하여 PDF 생성
    """
    try:
        image1 = Image.open(web_img_path).convert('RGB')
        image2 = Image.open(app_img_path).convert('RGB')

        # 높이는 둘 중 큰 것에 맞춤
        max_height = max(image1.height, image2.height)
        # 폭은 두 이미지 폭의 합
        total_width = image1.width + image2.width
        
        # 흰색 배경 캔버스 생성
        new_image = Image.new('RGB', (total_width, max_height), (255, 255, 255))
        
        # 붙여넣기 (좌측: Web, 우측: App)
        new_image.paste(image1, (0, 0))
        new_image.paste(image2, (image1.width, (max_height - image2.height) // 2)) # 앱 이미지는 세로 중앙 정렬
        
        new_image.save(output_pdf_path)
        print(f"📄 PDF 병합 완료: {output_pdf_path}")
    except Exception as e:
        print(f"❌ PDF 생성 실패: {e}")

def main():
    client = WebClient(token=SLACK_TOKEN)

    with sync_playwright() as p:
        print("🚀 브라우저 실행 중...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # ---------------------------------------------------------
        # [Step 0] 데이터 수집 (배너 리스트 파악)
        # ---------------------------------------------------------
        print(f"🌐 접속 중: {TARGET_URL}")
        page.goto(TARGET_URL)
        
        # 배너 로딩 대기
        try:
            page.wait_for_selector("li[class*='BannerArea_MainBannerArea__slider__slide']", state="visible", timeout=15000)
            time.sleep(2)
        except:
            print("❌ 배너 로딩 실패")
            browser.close()
            return

        # 배너 요소들 찾기
        slides = page.locator("li[class*='BannerArea_MainBannerArea__slider__slide']")
        count = slides.count()
        print(f"📊 총 {count}개의 배너 식별됨.")
        
        # 배너들의 ID(href)를 미리 저장해둠 (순서 보장용)
        banner_data = []
        for i in range(count):
            try:
                # i번째 슬라이드 내부 a 태그
                href = slides.nth(i).locator("a").get_attribute("href")
                banner_id = get_banner_id(href)
                banner_data.append({"index": i, "id": banner_id, "href": href})
            except:
                banner_data.append({"index": i, "id": f"unknown_{i}", "href": ""})

        # ---------------------------------------------------------
        # [Step 1] WEB 캡쳐 (순차적으로 '다음' 누르며 촬영)
        # ---------------------------------------------------------
        print("\n📸 [Phase 1] WEB 캡쳐 시작 (PC View)")
        # 높이를 1200으로 늘려 잘림 방지
        page.set_viewport_size({"width": 1920, "height": 1200})
        time.sleep(1)

        for i, item in enumerate(banner_data):
            # i가 0보다 크면 '다음' 버튼 눌러서 배너 넘기기
            if i > 0:
                try:
                    next_btn = page.locator('button[aria-label="다음"]').first
                    next_btn.click()
                    time.sleep(1.5) # 애니메이션 대기 (필수)
                except Exception as e:
                    print(f"⚠️ 다음 버튼 클릭 실패: {e}")

            # 캡쳐 (Web은 현재 뷰포트 그대로)
            file_web = f"web_{i}.png"
            page.screenshot(path=file_web)
            print(f"  - Web [{i+1}/{count}] {item['id']} 캡쳐됨")

        # ---------------------------------------------------------
        # [Step 2] APP 캡쳐 (요소 찾아가서 촬영)
        # ---------------------------------------------------------
        print("\n📸 [Phase 2] APP 캡쳐 시작 (Mobile View)")
        page.set_viewport_size({"width": 393, "height": 852}) # iPhone 14 Pro
        page.reload() # 페이지 새로고침 (Web에서 돌려놓은 슬라이드 초기화)
        
        # 모바일 로딩 대기
        try:
            page.wait_for_selector("li[class*='BannerArea_MainBannerArea__slider__slide']", state="visible", timeout=15000)
            time.sleep(2)
        except:
            pass # 이미 로딩되어 있을 수 있음

        for i, item in enumerate(banner_data):
            file_app = f"app_{i}.png"
            
            # 모바일에서는 '다음' 버튼 대신, 해당 요소로 스크롤 이동
            try:
                # 저장해둔 href를 가진 요소를 다시 찾음
                target_slide = page.locator(f"li[class*='BannerArea_MainBannerArea__slider__slide'] a[href='{item['href']}']").first
                
                # 해당 요소가 화면 중앙에 오도록 스크롤
                target_slide.scroll_into_view_if_needed()
                time.sleep(0.5) # 스크롤 안정화
                
                page.screenshot(path=file_app)
                print(f"  - App [{i+1}/{count}] {item['id']} 캡쳐됨")
            except Exception as e:
                print(f"❌ App 캡쳐 실패 ({item['id']}): {e}")
                # 실패 시 빈 이미지라도 생성 방지 등을 위해 pass

        # ---------------------------------------------------------
        # [Step 3] 병합 및 전송
        # ---------------------------------------------------------
        print("\n📤 [Phase 3] 병합 및 슬랙 전송")
        
        for i, item in enumerate(banner_data):
            web_png = f"web_{i}.png"
            app_png = f"app_{i}.png"
            
            today = datetime.now().strftime("%y%m%d")
            pdf_filename = f"{today}_{item['id']}_게재보고.pdf"

            if os.path.exists(web_png) and os.path.exists(app_png):
                # 좌우 병합 PDF 생성
                create_side_by_side_pdf(web_png, app_png, pdf_filename)
                
                # 슬랙 전송
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
                
                # 파일 정리
                os.remove(web_png)
                os.remove(app_png)
                if os.path.exists(pdf_filename): os.remove(pdf_filename)
            else:
                print(f"⚠️ 이미지 파일 누락으로 건너뜀: {item['id']}")

        print("\n✅ 모든 작업 완료!")
        browser.close()

if __name__ == "__main__":
    main()
