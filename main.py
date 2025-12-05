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

# --- [중요] 캡쳐 설정 ---
# Web: 너비는 1100 고정, 높이는 배너 끝부분에 맞춰 자동 조절 (잘림 방지)
WEB_WIDTH = 1100 
WEB_VIEWPORT_H = 1500 # 렌더링용 넉넉한 높이

# App: 아이폰 14 Pro 비율 등
APP_WIDTH, APP_HEIGHT = 353, 765

LAYOUT_GAP = 40 # PDF 병합 시 좌우 간격

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

        # 높이는 둘 중 큰 것에 맞춤 (보통 웹이 더 큼)
        max_height = max(image1.height, image2.height)
        total_width = image1.width + image2.width + LAYOUT_GAP
        
        # 흰색 배경 캔버스
        new_image = Image.new('RGB', (total_width, max_height), (255, 255, 255))
        
        # 웹(왼쪽), 앱(오른쪽) 배치
        new_image.paste(image1, (0, 0))
        new_image.paste(image2, (image1.width + LAYOUT_GAP, 0))
        
        # PDF 저장 (해상도 유지)
        new_image.save(output_pdf_path, "PDF", resolution=100.0, save_all=True)
        print(f"📄 PDF 생성 완료: {output_pdf_path}")
    except Exception as e:
        print(f"❌ PDF 생성 실패: {e}")

def handle_popup(page):
    """팝업 감지 및 닫기"""
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
        # Braze 등 마케팅 팝업 닫기
        if page.locator("div[class*='Modal']").is_visible() or page.locator("#carousel").is_visible():
            close_btn = page.locator("button[class*='close'], button[aria-label*='Close'], button[aria-label*='닫기']").first
            if close_btn.is_visible():
                close_btn.click()
            else:
                page.mouse.click(10, 10) # 좌표 클릭
            time.sleep(1)
    except Exception:
        pass

def main():
    client = WebClient(token=SLACK_TOKEN)

    with sync_playwright() as p:
        print("🚀 브라우저 실행 중 (고화질)...")
        browser = p.chromium.launch(headless=True)
        
        # [Web 컨텍스트] 고화질(2배율), 너비 1100 고정
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": WEB_WIDTH, "height": WEB_VIEWPORT_H},
            device_scale_factor=2
        )
        page = context.new_page()

        # ---------------------------------------------------------
        # [Step 1] 전체 배너 리스트 파악
        # ---------------------------------------------------------
        print(f"🌐 접속 중: {TARGET_URL}")
        page.goto(TARGET_URL)
        time.sleep(3)
        handle_popup(page)

        # 배너 섹션 로딩 대기
        try:
            page.wait_for_selector("li[class*='BannerArea_MainBannerArea__slider__slide']", state="visible", timeout=15000)
        except:
            print("❌ 배너 로딩 실패. 종료합니다.")
            browser.close()
            return

        # 전체 배너 개수 확인
        slides = page.locator("li[class*='BannerArea_MainBannerArea__slider__slide']")
        count = slides.count()
        print(f"📊 총 배너 수: {count}")

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
        # [Step 2] 배너별 탐색 (새로고침 -> 페이지네이션)
        # ---------------------------------------------------------
        for idx, target in enumerate(target_banners):
            print(f"\n--- [{idx+1}/{count}] 목표: {target['id']} 찾는 중 ---")
            found = False
            
            # (A) 전략 1: 새로고침 (Preload/Eager용) - 최대 10회
            refresh_limit = 10
            for r in range(refresh_limit):
                if r > 0: 
                    page.reload()
                    handle_popup(page)
                    try:
                        page.wait_for_selector("li[class*='BannerArea_MainBannerArea__slider__slide']", state="visible", timeout=5000)
                    except:
                        continue

                # 첫 번째 슬라이드 확인
                try:
                    first_slide = page.locator("li[class*='BannerArea_MainBannerArea__slider__slide']").first
                    first_href = first_slide.locator("a").get_attribute("href")
                    if target['href'] in first_href:
                        print(f"   ✨ [새로고침] {r+1}회 만에 발견!")
                        found = True
                        break
                except:
                    pass
            
            # (B) 전략 2: 페이지네이션 (Lazy용) - 새로고침으로 못 찾은 경우
            if not found:
                print(f"   ⚠️ 페이지네이션 탐색 시작 (Lazy 배너)")
                target_locator = page.locator(f"li[class*='BannerArea_MainBannerArea__slider__slide'] a[href='{target['href']}']")
                next_btn = page.locator('button[aria-label="다음"]').first
                
                max_clicks = 25
                for c in range(max_clicks):
                    if target_locator.is_visible():
                        print(f"   ✨ [페이지네이션] {c}번 이동 후 발견!")
                        found = True
                        break
                    
                    if next_btn.is_visible():
                        next_btn.click()
                        time.sleep(1)
                    else:
                        break

            # -----------------------------------------------------
            # [Step 3] 캡쳐 및 전송 (발견 시)
            # -----------------------------------------------------
            if found:
                web_png = f"web_{idx}.png"
                app_png = f"app_{idx}.png"
                pdf_filename = f"{datetime.now().strftime('%y%m%d')}_{target['id']}_게재보고.pdf"

                # (1) WEB 캡쳐 (자동 높이 조절)
                try:
                    page.set_viewport_size({"width": WEB_WIDTH, "height": WEB_VIEWPORT_H})
                    time.sleep(0.5)
                    handle_popup(page)
                    
                    # 배너 섹션의 바닥 좌표(Y) 계산 -> 정확한 Crop 높이 구하기 [핵심]
                    clip_height = page.evaluate("""() => {
                        // '지금 주목할 소식'이 포함된 섹션 전체를 찾거나, 슬라이더 컨테이너를 찾음
                        const slider = document.querySelector("div[class*='BannerArea_MainBannerArea__slider']");
                        if (slider) {
                            const rect = slider.getBoundingClientRect();
                            // 상단부터 슬라이더 바닥까지 + 여유분 20px
                            return rect.bottom + window.scrollY + 20; 
                        }
                        return 800; // 기본값
                    }""")
                    
                    # 만약 계산된 높이가 너무 작으면 최소 728 보장
                    final_height = max(clip_height, 728)
                    
                    print(f"     📸 Web 캡쳐 (1100 x {int(final_height)})")
                    page.screenshot(path=web_png, clip={"x": 0, "y": 0, "width": WEB_WIDTH, "height": final_height})
                    
                except Exception as e:
                    print(f"     ❌ Web 캡쳐 에러: {e}")

                # (2) APP 캡쳐 (뷰포트 353x765)
                try:
                    page.set_viewport_size({"width": APP_WIDTH, "height": APP_HEIGHT})
                    time.sleep(1)
                    handle_popup(page) # 모바일 팝업 제거
                    
                    print(f"     📸 App 캡쳐 ({APP_WIDTH} x {APP_HEIGHT})")
                    page.screenshot(path=app_png)
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
                page.set_viewport_size({"width": WEB_WIDTH, "height": WEB_VIEWPORT_H})
            else:
                print(f"   ❌ 결국 {target['id']}를 찾지 못했습니다. (건너뜀)")

        print("\n✅ 모든 작업 완료!")
        browser.close()

if __name__ == "__main__":
    main()
    
