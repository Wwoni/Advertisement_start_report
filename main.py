import os
import time
import re
from datetime import datetime
from playwright.sync_api import sync_playwright
from PIL import Image
from slack_sdk import WebClient

# --- 환경 변수 및 설정 ---
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL_ID")
TARGET_URL = "https://www.wanted.co.kr"

def get_banner_id(href):
    """
    링크에서 ID 숫자 또는 식별자를 추출합니다.
    """
    if not href:
        return "unknown"
    # URL 파라미터 제거 (? 이후)
    clean_path = href.split('?')[0]
    # 슬래시(/)로 나눈 뒤 가장 마지막 부분 추출
    segments = clean_path.split('/')
    # 혹시 마지막이 비어있다면(슬래시로 끝난 경우) 그 앞의 것 사용
    last_segment = segments[-1] if segments[-1] else segments[-2]
    return last_segment

def create_combined_pdf(web_img_path, app_img_path, output_pdf_path):
    """
    웹(상단) + 앱(하단) 이미지를 이어붙여 PDF로 저장합니다.
    """
    try:
        image1 = Image.open(web_img_path).convert('RGB')
        image2 = Image.open(app_img_path).convert('RGB')

        # 두 이미지 중 더 넓은 폭에 맞춤
        max_width = max(image1.width, image2.width)
        total_height = image1.height + image2.height
        
        # 흰색 배경 캔버스 생성
        new_image = Image.new('RGB', (max_width, total_height), (255, 255, 255))
        
        # 중앙 정렬하여 붙여넣기
        new_image.paste(image1, ((max_width - image1.width) // 2, 0))
        new_image.paste(image2, ((max_width - image2.width) // 2, image1.height))
        
        new_image.save(output_pdf_path)
        print(f"📄 PDF 병합 완료: {output_pdf_path}")
    except Exception as e:
        print(f"❌ PDF 생성 중 오류: {e}")

def main():
    # 슬랙 클라이언트 초기화
    client = WebClient(token=SLACK_TOKEN)

    with sync_playwright() as p:
        # 브라우저 실행 (headless=True는 화면 없이 실행)
        print("🚀 브라우저를 실행합니다...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # 1. 사이트 접속
        print(f"🌐 {TARGET_URL} 접속 중...")
        page.goto(TARGET_URL)

        # 2. 로딩 대기 (가장 중요한 수정 부분)
        # '지금 주목할 소식' 배너 리스트(li)가 뜰 때까지 최대 15초 기다립니다.
        # 부분 일치 선택자(*=)를 사용하여 클래스명이 조금 바뀌어도 찾을 수 있게 함
        try:
            print("⏳ 배너 로딩을 기다리는 중...")
            page.wait_for_selector("li[class*='BannerArea_MainBannerArea__slider__slide']", state="visible", timeout=15000)
            time.sleep(2) # 애니메이션 안정화를 위해 2초 추가 대기
        except Exception:
            print("❌ 배너 요소를 찾을 수 없습니다. (Timeout)")
            browser.close()
            return

        # 3. 배너 개수 파악
        slides = page.locator("li[class*='BannerArea_MainBannerArea__slider__slide']")
        count = slides.count()
        print(f"📊 총 {count}개의 배너를 발견했습니다.")

        if count == 0:
            print("❌ 배너 개수가 0개입니다. 선택자를 확인해주세요.")
            browser.close()
            return

        # 4. 반복 캡쳐 및 보고
        for i in range(count):
            print(f"\n--- [{i+1}/{count}] 번째 배너 작업 시작 ---")
            
            # (1) 현재 순서(i번째) 배너의 링크(ID) 추출
            # 주의: 화면에 보이는 것이 아니라 DOM 순서대로 가져옴 (대부분 일치)
            try:
                # i번째 슬라이드 내부의 a 태그 href 가져오기
                href = slides.nth(i).locator("a").get_attribute("href")
                banner_id = get_banner_id(href)
            except Exception as e:
                print(f"⚠️ ID 추출 실패 ({e}), 'unknown'으로 설정")
                banner_id = "unknown"

            today = datetime.now().strftime("%y%m%d")
            filename = f"{today}_{banner_id}_게재보고"
            web_png = f"web_{i}.png"
            app_png = f"app_{i}.png"
            pdf_path = f"{filename}.pdf"

            # (2) WEB 캡쳐 (PC 해상도)
            try:
                page.set_viewport_size({"width": 1920, "height": 1080})
                time.sleep(0.5) # 리사이징 대기
                page.screenshot(path=web_png)
                print("📸 Web 캡쳐 완료")
            except Exception as e:
                print(f"❌ Web 캡쳐 실패: {e}")

            # (3) APP 캡쳐 (모바일 해상도)
            try:
                page.set_viewport_size({"width": 393, "height": 852})
                time.sleep(0.5) # 리사이징 대기
                page.screenshot(path=app_png)
                print("📸 App 캡쳐 완료")
            except Exception as e:
                print(f"❌ App 캡쳐 실패: {e}")

            # (4) PDF 생성
            create_combined_pdf(web_png, app_png, pdf_path)

            # (5) 슬랙 전송
            if SLACK_TOKEN and SLACK_CHANNEL:
                try:
                    client.files_upload_v2(
                        channel=SLACK_CHANNEL,
                        file=pdf_path,
                        title=pdf_path,
                        initial_comment=f"📢 [{i+1}/{count}] 배너 게재 보고 : {banner_id}"
                    )
                    print(f"✅ 슬랙 전송 완료: {filename}")
                except Exception as e:
                    print(f"❌ 슬랙 전송 에러: {e}")
            else:
                print("⚠️ 슬랙 토큰이 설정되지 않아 전송을 건너뜁니다.")

            # (6) 다음 배너로 이동 ('다음' 버튼 클릭)
            # 버튼 클릭을 위해 다시 PC 뷰포트로 복귀 (버튼이 모바일에서 가려질 수 있음)
            page.set_viewport_size({"width": 1920, "height": 1080})
            time.sleep(0.5)
            
            try:
                # '다음' 버튼 찾기 (여러 개일 경우 첫 번째 것)
                next_button = page.locator('button[aria-label="다음"]').first
                if next_button.is_visible():
                    next_button.click()
                    print("➡️ '다음' 버튼 클릭함")
                    time.sleep(1.5) # 슬라이드 넘어가는 시간 대기
                else:
                    print("⚠️ '다음' 버튼을 찾을 수 없습니다. (마지막 배너일 수 있음)")
            except Exception as e:
                print(f"⚠️ 다음 버튼 클릭 중 오류: {e}")

            # (7) 임시 파일 삭제 (청소)
            if os.path.exists(web_png): os.remove(web_png)
            if os.path.exists(app_png): os.remove(app_png)
            if os.path.exists(pdf_path): os.remove(pdf_path)

        print("\n✅ 모든 작업이 완료되었습니다.")
        browser.close()

if __name__ == "__main__":
    main()
