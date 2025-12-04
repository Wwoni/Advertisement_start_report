import os
import time
import re
from datetime import datetime
from playwright.sync_api import sync_playwright
from PIL import Image
from slack_sdk import WebClient

# --- 설정값 (Secrets에서 불러옴) ---
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL_ID")
TARGET_URL = "https://www.wanted.co.kr" # 실제 URL로 변경 필요

def get_banner_id(href):
    """
    URL에서 ID만 추출합니다.
    예: /company/1311 -> 1311
    예: /wd/324596 -> 324596
    """
    if not href:
        return "unknown"
    # URL에서 ? 뒷부분(파라미터) 제거 및 /로 분리
    clean_path = href.split('?')[0]
    segments = clean_path.split('/')
    # 마지막 부분이 숫자면 그것을, 아니면 마지막 문자열 반환
    last_segment = segments[-1] if segments[-1] else segments[-2]
    return last_segment

def create_combined_pdf(web_img_path, app_img_path, output_pdf_path):
    """
    웹 이미지(위)와 앱 이미지(아래)를 합쳐 PDF로 저장합니다.
    """
    image1 = Image.open(web_img_path).convert('RGB')
    image2 = Image.open(app_img_path).convert('RGB')

    # 두 이미지 중 넓은 폭에 맞춤
    max_width = max(image1.width, image2.width)
    
    # 세로로 이어 붙이기 위한 캔버스 생성
    total_height = image1.height + image2.height
    new_image = Image.new('RGB', (max_width, total_height), (255, 255, 255))
    
    # 붙여넣기 (가운데 정렬)
    new_image.paste(image1, ((max_width - image1.width) // 2, 0))
    new_image.paste(image2, ((max_width - image2.width) // 2, image1.height))
    
    new_image.save(output_pdf_path)
    print(f"📄 PDF 생성 완료: {output_pdf_path}")

def main():
    client = WebClient(token=SLACK_TOKEN)
    
    with sync_playwright() as p:
        # 브라우저 실행
        browser = p.chromium.launch(headless=True) # 디버깅 시 headless=False
        page = browser.new_page()
        
        # 1. 사이트 접속
        print("🌐 사이트 접속 중...")
        page.goto(TARGET_URL)
        page.wait_for_load_state("networkidle")
        time.sleep(3) # 확실한 로딩 대기

        # 2. 배너 개수 파악
        # 제공해주신 HTML 클래스 참고
        slides = page.locator("li.BannerArea_MainBannerArea__slider__slide__4t0MH")
        count = slides.count()
        print(f"📊 총 {count}개의 배너를 발견했습니다.")

        # 3. 반복 캡쳐 시작
        for i in range(count):
            print(f"--- [{i+1}/{count}] 번째 배너 처리 중 ---")
            
            # (1) 현재 가장 왼쪽(활성화된) 배너 정보 가져오기
            # '다음' 버튼을 누르면 DOM 순서가 바뀌거나 transform이 변함.
            # 가장 확실한 방법: 화면상 보이는 첫번째 슬라이드 타겟팅
            # 여기서는 i번째 슬라이드가 아니라, 현재 뷰포트에 보이는 첫번째 슬라이드를 가져와야 함
            # 다만, Wanted 사이트 특성상 DOM이 회전할 수 있으므로, 
            # 단순히 루프 돌며 '현재 보이는 것'을 찍고 '다음'을 누르는 방식 채택
            
            # 현재 활성화된(보이는) 첫번째 슬라이드 찾기 (복잡하면 단순히 nth(0)가 아닐 수 있음, 
            # 하지만 제공된 로직상 '왼쪽에 위치했을 때' 이므로 화면 캡쳐 위주로 진행)
            
            # 링크(href) 추출을 위해 현재 슬라이드 특정
            # 보통 슬라이더 라이브러리는 'active' 클래스를 주거나, 순서대로 정렬됨.
            # 여기서는 단순히 slides.nth(i)를 쓰기보다, 현재 화면에 노출된 요소의 링크를 가져오는 것이 안전.
            # 하지만 구현 편의상, 코드 구조상 slides.nth(i) 로 접근하되,
            # 실제 링크 값은 스크립트로 추출
            
            # 현재 가장 왼쪽 슬라이드의 a태그 찾기
            # (화면상 보이는 첫번째 슬라이드의 a 태그 href 가져오기)
            # 복잡한 DOM 구조 대신, JS로 첫번째 슬라이드 데이터 추출
            href = page.evaluate("""() => {
                const slides = document.querySelectorAll('li.BannerArea_MainBannerArea__slider__slide__4t0MH');
                // 현재 DOM 상 첫번째 혹은 화면 내 첫번째 요소 반환
                return slides[0].querySelector('a').getAttribute('href');
            }""")
            
            banner_id = get_banner_id(href)
            today = datetime.now().strftime("%y%m%d")
            filename = f"{today}_{banner_id}_게재보고"
            
            # (2) WEB 캡쳐 (PC 1920x1080)
            page.set_viewport_size({"width": 1920, "height": 1080})
            time.sleep(1) # 리사이징 대기
            web_png = f"web_{i}.png"
            # 전체 페이지 말고 뷰포트만 찍을지, 특정 영역만 찍을지 결정. 여기선 뷰포트 캡쳐
            page.screenshot(path=web_png)
            
            # (3) APP 캡쳐 (iPhone 14 Pro: 393x852)
            page.set_viewport_size({"width": 393, "height": 852})
            time.sleep(1) # 모바일 레이아웃 적응 대기
            app_png = f"app_{i}.png"
            page.screenshot(path=app_png)
            
            # (4) PDF 병합
            pdf_path = f"{filename}.pdf"
            create_combined_pdf(web_png, app_png, pdf_path)
            
            # (5) 슬랙 전송
            try:
                client.files_upload_v2(
                    channel=SLACK_CHANNEL,
                    file=pdf_path,
                    title=pdf_path,
                    initial_comment=f"📢 [{i+1}/{count}] {banner_id}번 배너 게재 보고입니다."
                )
                print(f"✅ 슬랙 전송 완료: {banner_id}")
            except Exception as e:
                print(f"❌ 슬랙 전송 실패: {e}")

            # (6) 다음 배너로 이동 (PC 뷰로 복귀 후 클릭 권장)
            page.set_viewport_size({"width": 1920, "height": 1080})
            time.sleep(0.5)
            
            # 다음 버튼 클릭
            next_button = page.locator('button[aria-label="다음"]')
            if next_button.is_visible():
                next_button.click()
                time.sleep(2) # 슬라이드 애니메이션 대기
            else:
                print("⚠️ 다음 버튼을 찾을 수 없습니다. 종료합니다.")
                break
                
            # 임시 파일 정리
            os.remove(web_png)
            os.remove(app_png)
            os.remove(pdf_path)

        browser.close()

if __name__ == "__main__":
    main()
