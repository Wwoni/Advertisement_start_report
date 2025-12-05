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
# Web: 3개 노출 보장을 위해 1480px로 넉넉하게 열고, 결과물은 1100px로 맞춤
WEB_VIEWPORT_W = 1480 
WEB_TARGET_WIDTH = 1100 # PDF에 들어갈 최종 너비
WEB_RENDER_HEIGHT = 2000

# App: 캡쳐는 353px 뷰포트로 하고, PDF에 넣을 때도 이 사이즈를 유지
APP_WIDTH = 353
APP_HEIGHT = 765
APP_TARGET_WIDTH = 353 # PDF에 들어갈 최종 너비

LAYOUT_GAP = 40 # PDF 좌우 간격

def get_banner_id(href):
    if not href: return "unknown"
    clean_path = href.split('?')[0]
    segments = clean_path.split('/')
    return segments[-1] if segments[-1] else segments[-2]

def resize_image_high_quality(image_path, target_width):
    """이미지를 고화질(LANCZOS)로 리사이징하고 높이를 반환"""
    try:
        img = Image.open(image_path)
        # 비율 계산
        w_percent = (target_width / float(img.size[0]))
        h_size = int((float(img.size[1]) * float(w_percent)))
        
        # 고품질 리사이징
        img = img.resize((target_width, h_size), Image.Resampling.LANCZOS)
        img.save(image_path, quality=95) # 화질 95% 저장
        return h_size
    except Exception as e:
        print(f"⚠️ 리사이징 실패: {e}")
        return 0

def create_custom_layout_pdf(web_img_path, app_img_path, output_pdf_path):
    """[웹(1100)] [간격] [앱(353)] 배치로 PDF 생성"""
    try:
        # 이미지는 위에서 resize_image_high_quality로 이미 사이즈가 조정된 상태임
        image1 = Image.open(web_img_path).convert('RGB')
        image2 = Image.open(app_img_path).convert('RGB')

        max_height = max(image1.height, image2.height)
        total_width = image1.width + image2.width + LAYOUT_GAP
        
        # 흰색 배경 캔버스
        new_image = Image.new('RGB', (total_width, max_height), (255, 255, 255))
        
        # 웹(왼쪽) 배치
        new_image.paste(image1, (0, 0))
        # 앱(오른쪽) 배치 - 상단 정렬
        new_image.paste(image2, (image1.width + LAYOUT_GAP, 0))
        
        # PDF 저장 (해상도 유지)
        new_image.save(output_pdf_path, "PDF", resolution=100.0, save_all=True)
        print(f"📄 PDF 생성 완료: {output_pdf_path}")
    except Exception as e:
        print(f"❌ PDF 생성 실패: {e}")

def handle_popup(page):
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
        # 다양한 팝업/모달 닫기 시도
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
            return rect.bottom + window.scrollY + 60; // 여유분 60px
        }}
        return {min_height};
    }}""")

def main():
    client = WebClient(token=SLACK_TOKEN)

    with sync_playwright() as p:
        print("🚀 브라우저 실행 중 (고화질)...")
        browser = p.chromium.launch(headless=True)
        
        # [Web 컨텍스트] 3개 노출을 위해 1480px로 시작 (2배율 고화질)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": WEB_VIEWPORT_W, "height": WEB_RENDER_HEIGHT},
            device_scale_factor=2
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
            
            # (A) 전략 1: 새로고침 (Preload/Eager용) - 최대 10회
            for r in range(10):
                if r > 0: 
                    page.reload()
                    handle_popup(page)
                    try:
                        page.wait_for_selector("li[class*='BannerArea_MainBannerArea__slider__slide']", state="visible", timeout=5000)
                    except:
                        continue

                try:
                    first_slide = page.locator("li[class*='BannerArea_MainBannerArea__slider__slide']").first
                    first_href = first_slide.locator("a").get_attribute("href")
                    if target['href'] in first_href:
                        print(f"   ✨ [새로고침] {r+1}회 만에 발견!")
                        found = True
                        break
                except:
                    pass
            
            # (B) 전략 2: 페이지네이션 (Lazy용)
            if not found:
                print(f"   ⚠️ 페이지네이션 탐색 시작")
                target_locator = page.locator(f"li[class*='BannerArea_MainBannerArea__slider__slide'] a[href='{target['href']}']")
                next_btn = page.locator('button[aria-label="다음"]').first
                
                for c in range(25): # 최대 25번 클릭
                    # 위치 검증 (화면 좌측에 왔는지)
                    if target_locator.count() > 0:
                        box = target_locator.first.bounding_box()
                        if box and 0 <= box['x'] < 300:
                            print(f"   ✨ [페이지네이션] {c}번 이동 후 화면 노출 확인!")
                            found = True
                            break
                    
                    # [핵심 수정] 버튼이 활성화(Enabled) 상태인지 확인 후 클릭
                    if next_btn.is_visible() and next_btn.is_enabled():
                        next_btn.click()
                        time.sleep(1.5)
                    else:
                        print("   ⛔ 더 이상 '다음'으로 이동할 수 없습니다. (마지막 슬라이드)")
                        break

            # 3. 캡쳐 및 전송
            if found:
                web_png = f"web_{idx}.png"
                app_png = f"app_{idx}.png"
                pdf_filename = f"{datetime.now().strftime('%y%m%d')}_{target['id']}_게재보고.pdf"

                # (1) WEB 캡쳐 (1480px -> 1100px 리사이징)
                try:
                    page.set_viewport_size({"width": WEB_VIEWPORT_W, "height": WEB_RENDER_HEIGHT})
                    time.sleep(0.5)
                    handle_popup(page)
                    
                    clip_height = get_dynamic_clip_height(page, 800)
                    
                    # 3개 노출을 위해 1480px 폭으로 찍음
                    page.screenshot(path=web_png, clip={"x": 0, "y": 0, "width": WEB_VIEWPORT_W, "height": clip_height})
                    
                    # 찍은 후 1100px로 리사이징 (파일 크기 및 PDF 배치 최적화)
                    resize_image_high_quality(web_png, WEB_TARGET_WIDTH)
                    print(f"     📸 Web 캡쳐 완료")
                except Exception as e:
                    print(f"     ❌ Web 캡쳐 에러: {e}")

                # (2) APP 캡쳐 (353px 사이즈 맞춤)
                try:
                    page.set_viewport_size({"width": APP_WIDTH, "height": APP_HEIGHT})
                    time.sleep(1)
                    handle_popup(page)
                    
                    mobile_clip_height = get_dynamic_clip_height(page, 765)
                    page.set_viewport_size({"width": APP_WIDTH, "height": int(mobile_clip_height + 100)})
                    
                    page.screenshot(path=app_png, clip={"x": 0, "y": 0, "width": APP_WIDTH, "height": mobile_clip_height})
                    
                    # 모바일 이미지도 정확한 353px 너비로 리사이징 (고화질 유지)
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
                
                # Web 사이즈 복구
                page.set_viewport_size({"width": WEB_VIEWPORT_W, "height": WEB_RENDER_HEIGHT})
            else:
                print(f"   ❌ {target['id']} 미발견 (Skip)")

        print("\n✅ 모든 작업 완료!")
        browser.close()

if __name__ == "__main__":
    main()
