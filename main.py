import os
import time
from datetime import datetime
from playwright.sync_api import sync_playwright
from PIL import Image
from slack_sdk import WebClient

# --- 환경 변수 ---
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL_ID")
TARGET_URL = "https://www.wanted.co.kr"

# --- [초고화질 및 레이아웃 설정] ---
# Web: 1920px (3개 노출 보장) * 3배율 = 5760px 원본 캡쳐
WEB_VIEWPORT_W = 1920
WEB_RENDER_HEIGHT = 2500
WEB_TARGET_WIDTH = 1100 # 결과물 리사이징 (파일 용량 관리)

# App: 400px * 3배율 = 1200px 원본 캡쳐
APP_VIEWPORT_W = 400
APP_VIEWPORT_H = 1000
APP_TARGET_WIDTH = 320 # 컴팩트 사이즈

LAYOUT_GAP = 20 

def get_banner_id(href):
    if not href: return "unknown"
    clean_path = href.split('?')[0]
    segments = clean_path.split('/')
    return segments[-1] if segments[-1] else segments[-2]

def resize_image_high_quality(image_path, target_width):
    """LANCZOS 필터 + 최고 화질 옵션으로 리사이징"""
    try:
        img = Image.open(image_path)
        # 비율 유지 계산
        w_percent = (target_width / float(img.size[0]))
        h_size = int((float(img.size[1]) * float(w_percent)))
        
        # 고품질 리사이징
        img = img.resize((target_width, h_size), Image.Resampling.LANCZOS)
        # 품질 100, 서브샘플링 0 (색상/텍스트 깨짐 방지)
        img.save(image_path, quality=100, subsampling=0)
        return h_size
    except Exception as e:
        print(f"⚠️ 리사이징 오류: {e}")
        return 0

def create_custom_layout_pdf(web_img_path, app_img_path, output_pdf_path):
    """[웹] [간격] [앱] 좌측 정렬 배치"""
    try:
        image1 = Image.open(web_img_path).convert('RGB')
        image2 = Image.open(app_img_path).convert('RGB')

        max_height = max(image1.height, image2.height)
        total_width = image1.width + image2.width + LAYOUT_GAP
        
        new_image = Image.new('RGB', (total_width, max_height), (255, 255, 255))
        
        # 웹 (0,0)
        new_image.paste(image1, (0, 0))
        # 앱 (웹 바로 우측)
        new_image.paste(image2, (image1.width + LAYOUT_GAP, 0))
        
        # 300 DPI 고해상도 PDF 저장
        new_image.save(output_pdf_path, "PDF", resolution=300.0, save_all=True)
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
    except:
        pass

def get_dynamic_clip_height(page, selector, min_height):
    return page.evaluate(f"""() => {{
        const el = document.querySelector("{selector}");
        if (el) {{
            const rect = el.getBoundingClientRect();
            // 배너 바닥 + 60px 여유
            return rect.bottom + window.scrollY + 60; 
        }}
        return {min_height};
    }}""")

def main():
    client = WebClient(token=SLACK_TOKEN)

    with sync_playwright() as p:
        print("🚀 브라우저 실행 (3배율 초고화질)...")
        browser = p.chromium.launch(headless=True)
        
        # ------------------------------------------------------------------
        # [Step 1] Web 캡쳐 (PC, 1920px) - 순차 주행 모드
        # ------------------------------------------------------------------
        context_web = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": WEB_VIEWPORT_W, "height": WEB_RENDER_HEIGHT},
            device_scale_factor=3.0 # Retina급 화질
        )
        page_web = context_web.new_page()
        
        print(f"🌐 [Web] 접속 중: {TARGET_URL}")
        page_web.goto(TARGET_URL)
        time.sleep(3)
        handle_popup(page_web)

        # 배너 요소 파악
        try:
            page_web.wait_for_selector("li[class*='BannerArea_MainBannerArea__slider__slide']", state="visible", timeout=15000)
        except:
            print("❌ 배너 로딩 실패")
            browser.close()
            return

        slides = page_web.locator("li[class*='BannerArea_MainBannerArea__slider__slide']")
        count = slides.count()
        print(f"📊 총 {count}개의 배너 발견 (Web)")

        # ID 리스트 확보
        target_infos = []
        for i in range(count):
            try:
                href = slides.nth(i).locator("a").get_attribute("href")
                banner_id = get_banner_id(href)
                target_infos.append({"index": i, "id": banner_id, "href": href})
            except:
                pass
        
        # Web 캡쳐 진행 (새로고침 없이 '다음' 버튼만 누르며 전진)
        next_btn = page_web.locator('button[aria-label="다음"]').first
        
        for idx, target in enumerate(target_infos):
            print(f"\n📸 [Web] {idx+1}/{count} - {target['id']} 위치 찾는 중...")
            
            # 1. 목표 배너가 화면 맨 왼쪽(0~500px)에 올 때까지 이동
            target_locator = page_web.locator(f"li[class*='BannerArea_MainBannerArea__slider__slide'] a[href='{target['href']}']")
            
            found_web = False
            for c in range(20): # 최대 20번 클릭 시도
                if target_locator.count() > 0:
                    box = target_locator.first.bounding_box()
                    if box and 0 <= box['x'] < 500: # 발견!
                        found_web = True
                        break
                
                # 아직 안 보이면 '다음' 클릭
                if next_btn.is_visible() and not next_btn.is_disabled():
                    next_btn.click()
                    time.sleep(0.8) # 애니메이션 대기
                else:
                    break # 더 갈 곳 없음
            
            if found_web:
                # Web 캡쳐
                clip_h = get_dynamic_clip_height(page_web, "ul[class*='BannerArea_MainBannerArea__slider']", 800)
                web_filename = f"web_{idx}.png"
                page_web.screenshot(path=web_filename, clip={"x": 0, "y": 0, "width": WEB_VIEWPORT_W, "height": clip_h})
                # 리사이징 (화질 유지)
                resize_image_high_quality(web_filename, WEB_TARGET_WIDTH)
                print(f"   ✅ Web 캡쳐 완료")
            else:
                print(f"   ❌ Web에서 배너를 찾지 못함 (Skip)")

        # ------------------------------------------------------------------
        # [Step 2] App 캡쳐 (Mobile) - 스크롤 모드
        # ------------------------------------------------------------------
        context_app = browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
            viewport={"width": APP_VIEWPORT_W, "height": APP_VIEWPORT_H},
            device_scale_factor=3.0,
            is_mobile=True
        )
        page_app = context_app.new_page()
        
        print(f"\n🌐 [App] 접속 중...")
        page_app.goto(TARGET_URL)
        time.sleep(2)
        handle_popup(page_app)

        for idx, target in enumerate(target_infos):
            # Web 캡쳐 성공한 것만 App도 찍음
            web_filename = f"web_{idx}.png"
            if not os.path.exists(web_filename):
                continue

            print(f"📸 [App] {target['id']} 찾는 중...")
            
            try:
                target_locator = page_app.locator(f"li[class*='BannerArea_MainBannerArea__slider__slide'] a[href='{target['href']}']").first
                
                # 스크롤 이동
                target_locator.scroll_into_view_if_needed()
                time.sleep(0.5)
                
                # 높이 계산 및 뷰포트 확장 (잘림 방지)
                clip_h = get_dynamic_clip_height(page_app, "ul[class*='BannerArea_MainBannerArea__slider']", 765)
                page_app.set_viewport_size({"width": APP_VIEWPORT_W, "height": int(clip_h + 100)})
                
                app_filename = f"app_{idx}.png"
                page_app.screenshot(path=app_filename, clip={"x": 0, "y": 0, "width": APP_VIEWPORT_W, "height": clip_h})
                
                # 리사이징
                resize_image_high_quality(app_filename, APP_TARGET_WIDTH)
                print(f"   ✅ App 캡쳐 완료")
                
                # [Step 3] PDF 생성 및 전송
                pdf_filename = f"{datetime.now().strftime('%y%m%d')}_{target['id']}_게재보고.pdf"
                create_custom_layout_pdf(web_filename, app_filename, pdf_filename)
                
                if SLACK_TOKEN and SLACK_CHANNEL:
                    client.files_upload_v2(
                        channel=SLACK_CHANNEL,
                        file=pdf_filename,
                        title=pdf_filename,
                        initial_comment=f"📢 [{idx+1}/{count}] {target['id']} 게재 보고"
                    )
                    print(f"   🚀 슬랙 전송 완료")
                
                # 청소
                if os.path.exists(web_filename): os.remove(web_filename)
                if os.path.exists(app_filename): os.remove(app_filename)
                if os.path.exists(pdf_filename): os.remove(pdf_filename)

            except Exception as e:
                print(f"   ❌ App 처리 중 오류: {e}")

        print("\n✅ 모든 작업 완료!")
        browser.close()

if __name__ == "__main__":
    main() 
