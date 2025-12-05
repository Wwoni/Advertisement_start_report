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
WEB_VIEWPORT_W = 1920
WEB_RENDER_HEIGHT = 2500
WEB_TARGET_WIDTH = 1100  # 결과물 리사이징 (파일 용량 관리)

APP_VIEWPORT_W = 400
APP_VIEWPORT_H = 1000
APP_TARGET_WIDTH = 320    # 컴팩트 사이즈

LAYOUT_GAP = 20


# =========================
#  공통 유틸 함수
# =========================
def get_banner_id(href: str) -> str:
    if not href:
        return "unknown"
    clean_path = href.split("?")[0]
    segments = clean_path.split("/")
    return segments[-1] if segments[-1] else segments[-2]


def resize_image_high_quality(image_path, target_width):
    """LANCZOS 필터 + 최고 화질 옵션으로 리사이징"""
    try:
        img = Image.open(image_path)
        img = img.convert("RGB")  # PDF 저장을 위해 RGB 통일

        w_percent = (target_width / float(img.size[0]))
        h_size = int((float(img.size[1]) * float(w_percent)))

        img = img.resize((target_width, h_size), Image.Resampling.LANCZOS)
        # 품질 95, 서브샘플링 0 (텍스트/색상 유지)
        img.save(image_path, format="JPEG", quality=95, subsampling=0)
        return h_size
    except Exception as e:
        print(f"⚠️ 리사이징 오류: {e}")
        return 0


def create_custom_layout_pdf(web_img_path, app_img_path, output_pdf_path):
    """[웹] [간격] [앱] 좌측 정렬 배치 후 PDF 저장"""
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
    """접속 시 뜨는 모달/팝업 닫기"""
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
        if page.locator("div[class*='Modal']").is_visible() or page.locator("#carousel").is_visible():
            close_btn = page.locator(
                "button[class*='close'], button[aria-label*='Close'], button[aria-label*='닫기']"
            ).first
            if close_btn.is_visible():
                close_btn.click()
            else:
                page.mouse.click(10, 10)
            time.sleep(1)
    except Exception:
        pass


def get_dynamic_clip_height(page, selector, min_height):
    """지정 selector의 bottom 기준으로 캡쳐 높이 동적 계산"""
    return page.evaluate(f"""() => {{
        const el = document.querySelector("{selector}");
        if (el) {{
            const rect = el.getBoundingClientRect();
            // 배너 바닥 + 60px 여유
            return rect.bottom + window.scrollY + 60; 
        }}
        return {min_height};
    }}""")


# =========================
#  Lazy 포함 배너 포지션 감지용
# =========================
def get_leftmost_banner_id(page):
    """
    현재 뷰포트에 보이는 슬라이드 중 '가장 왼쪽' 배너의 ID 반환
    (lazy 포함, 새로고침 없이 상태 기준)
    """
    js = """
    () => {
        const slides = Array.from(
            document.querySelectorAll("li[class*='BannerArea_MainBannerArea__slider__slide']")
        );
        if (!slides.length) return null;

        const visible = slides
          .map(el => ({ el, rect: el.getBoundingClientRect() }))
          .filter(s =>
              s.rect.width > 0 &&
              s.rect.right > 0 &&
              s.rect.left < window.innerWidth
          );

        if (!visible.length) return null;

        visible.sort((a, b) => a.rect.left - b.rect.left);
        const leftMost = visible[0].el;
        const a = leftMost.querySelector("a");
        if (!a || !a.getAttribute("href")) return null;

        const href = a.getAttribute("href");
        const clean = href.split("?")[0];
        const segments = clean.split("/");
        const last = segments[segments.length - 1] || segments[segments.length - 2];
        return last || null;
    }
    """
    return page.evaluate(js)


def move_to_banner(page, target_banner_id, next_btn, max_clicks=50, wait_ms=900):
    """
    '다음' 버튼만 순차 클릭하면서,
    target_banner_id가 '화면에서 가장 왼쪽'에 올 때까지 이동.

    반환값:
      - True: 타겟 배너가 왼쪽에 도달
      - False: 한 바퀴 돌아도 못 찾음
    """
    start_id = get_leftmost_banner_id(page)
    print(f"[DEBUG] 시작 왼쪽 배너 ID: {start_id}")

    for i in range(max_clicks):
        current_id = get_leftmost_banner_id(page)
        print(f"[DEBUG] click {i}, 현재 왼쪽 배너 = {current_id}")

        if current_id == target_banner_id:
            print(f"[INFO] target {target_banner_id} FOUND at leftmost after {i} clicks")
            return True

        # 한 바퀴 돌았는데도 못 찾으면 종료
        if i > 0 and start_id is not None and current_id == start_id:
            print(f"[WARN] 한 바퀴 돌았지만 {target_banner_id}를 찾지 못했습니다.")
            return False

        try:
            if not next_btn.is_visible():
                print("[WARN] '다음' 버튼이 더 이상 보이지 않습니다.")
                return False
            next_btn.click()
        except Exception as e:
            print(f"[ERROR] next 버튼 클릭 실패: {e}")
            return False

        page.wait_for_timeout(wait_ms)

    print(f"[WARN] max_clicks={max_clicks} 내에 {target_banner_id}를 찾지 못했습니다.")
    return False


def capture_web_banner_area(page, image_path):
    """
    Web 메인 배너 영역만 캡쳐.
    section 기준으로 안 잡히면 기존 clip 방식 fallback.
    """
    try:
        container = page.locator("section[class*='BannerArea_MainBannerArea']").first
        if container and container.count() > 0:
            container.screenshot(path=image_path)
            return

        # fallback: 상단 영역 clip
        clip_h = get_dynamic_clip_height(
            page,
            "ul[class*='BannerArea_MainBannerArea__slider']",
            800
        )
        page.screenshot(
            path=image_path,
            clip={"x": 0, "y": 0, "width": WEB_VIEWPORT_W, "height": clip_h}
        )
    except Exception as e:
        print(f"⚠️ Web 배너 캡쳐 실패, full_page fallback 사용: {e}")
        page.screenshot(path=image_path, full_page=True)


def main():
    client = WebClient(token=SLACK_TOKEN)

    with sync_playwright() as p:
        print("🚀 브라우저 실행 (고화질 모드)...")
        browser = p.chromium.launch(headless=True)

        # ------------------------------------------------------------------
        # [Step 1] Web 캡쳐 (PC, 1920px) - lazy 포함 순차 주행 모드
        # ------------------------------------------------------------------
        context_web = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": WEB_VIEWPORT_W, "height": WEB_RENDER_HEIGHT},
            device_scale_factor=2.5  # 3.0 → 2.5로 조정 (리사이즈시 선명도+용량 균형)
        )
        page_web = context_web.new_page()

        print(f"🌐 [Web] 접속 중: {TARGET_URL}")
        page_web.goto(TARGET_URL)
        time.sleep(3)
        handle_popup(page_web)

        # 배너 요소 파악
        try:
            page_web.wait_for_selector(
                "li[class*='BannerArea_MainBannerArea__slider__slide']",
                state="visible",
                timeout=15000
            )
        except Exception:
            print("❌ 배너 로딩 실패")
            browser.close()
            return

        slides = page_web.locator("li[class*='BannerArea_MainBannerArea__slider__slide']")
        count = slides.count()
        print(f"📊 총 {count}개의 슬라이드 DOM 발견 (Web)")

        # ID 리스트 확보 (중복 제거)
        target_infos = []
        seen_ids = set()
        for i in range(count):
            try:
                href = slides.nth(i).locator("a").get_attribute("href")
                if not href:
                    continue
                banner_id = get_banner_id(href)
                if banner_id in seen_ids:
                    continue
                seen_ids.add(banner_id)
                target_infos.append({"id": banner_id, "href": href})
            except Exception as e:
                print(f"⚠️ slide {i} 처리 중 오류: {e}")

        print(f"📊 최종 캡쳐 대상 배너 수: {len(target_infos)}")
        print("   IDs:", [t['id'] for t in target_infos])

        next_btn = page_web.locator('button[aria-label="다음"]').first

        # Web 캡쳐 진행 (새로고침 없이 '다음' 버튼만 누르며 전진)
        for idx, target in enumerate(target_infos):
            banner_id = target["id"]
            print(f"\n📸 [Web] {idx+1}/{len(target_infos)} - {banner_id} 위치 찾는 중...")

            found_web = move_to_banner(page_web, banner_id, next_btn)

            if found_web:
                web_filename = f"web_{idx}.jpg"
                capture_web_banner_area(page_web, web_filename)
                resize_image_high_quality(web_filename, WEB_TARGET_WIDTH)
                print(f"   ✅ Web 캡쳐 완료: {web_filename}")
            else:
                print(f"   ❌ Web에서 배너 {banner_id}를 찾지 못함 (Skip)")

        # ------------------------------------------------------------------
        # [Step 2] App 캡쳐 (Mobile) - 기존 스크롤 모드 유지
        # ------------------------------------------------------------------
        context_app = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/16.0 Mobile/15E148 Safari/604.1"
            ),
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
            banner_id = target["id"]
            web_filename = f"web_{idx}.jpg"

            # Web 캡쳐 성공한 것만 App도 진행
            if not os.path.exists(web_filename):
                print(f"\n📸 [App] {banner_id} - Web 캡쳐 실패로 App 스킵")
                continue

            print(f"\n📸 [App] {banner_id} 찾는 중...")

            try:
                # href 기준으로 동일 배너 찾기 (모바일에선 lazy/구조에 따라 실패 가능 → 필요시 여기도 move_to_* 응용)
                target_locator = page_app.locator(
                    f"li[class*='BannerArea_MainBannerArea__slider__slide'] a[href*='{banner_id}']"
                ).first

                if not target_locator or target_locator.count() == 0:
                    # href 전체 일치가 안 될 수 있어서 ID 포함 매칭으로 완화
                    print(f"   ⚠️ App에서 href로 {banner_id}를 직접 찾지 못함, 상단 영역 캡쳐로 대체")
                    clip_h = get_dynamic_clip_height(
                        page_app,
                        "ul[class*='BannerArea_MainBannerArea__slider']",
                        765
                    )
                    page_app.set_viewport_size({"width": APP_VIEWPORT_W, "height": int(clip_h + 100)})
                    app_filename = f"app_{idx}.jpg"
                    page_app.screenshot(
                        path=app_filename,
                        clip={"x": 0, "y": 0, "width": APP_VIEWPORT_W, "height": clip_h}
                    )
                else:
                    # 타겟 배너가 포함된 영역으로 스크롤 후 캡쳐
                    target_locator.scroll_into_view_if_needed()
                    time.sleep(0.5)

                    clip_h = get_dynamic_clip_height(
                        page_app,
                        "ul[class*='BannerArea_MainBannerArea__slider']",
                        765
                    )
                    page_app.set_viewport_size({"width": APP_VIEWPORT_W, "height": int(clip_h + 100)})

                    app_filename = f"app_{idx}.jpg"
                    page_app.screenshot(
                        path=app_filename,
                        clip={"x": 0, "y": 0, "width": APP_VIEWPORT_W, "height": clip_h}
                    )

                # 리사이징
                resize_image_high_quality(app_filename, APP_TARGET_WIDTH)
                print(f"   ✅ App 캡쳐 완료: {app_filename}")

                # [Step 3] PDF 생성 및 전송
                pdf_filename = f"{datetime.now().strftime('%y%m%d')}_{banner_id}_게재보고.pdf"
                create_custom_layout_pdf(web_filename, app_filename, pdf_filename)

                if SLACK_TOKEN and SLACK_CHANNEL:
                    client.files_upload_v2(
                        channel=SLACK_CHANNEL,
                        file=pdf_filename,
                        title=pdf_filename,
                        initial_comment=f"📢 [{idx+1}/{len(target_infos)}] {banner_id} 게재 보고"
                    )
                    print(f"   🚀 슬랙 전송 완료")

                # 청소
                for f in (web_filename, app_filename, pdf_filename):
                    if os.path.exists(f):
                        os.remove(f)

            except Exception as e:
                print(f"   ❌ App 처리 중 오류: {e}")

        print("\n✅ 모든 작업 완료!")
        browser.close()


if __name__ == "__main__":
    main()
