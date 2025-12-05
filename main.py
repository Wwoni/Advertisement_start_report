import os
import time
from datetime import datetime
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont
from slack_sdk import WebClient

# --- 환경 변수 ---
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL_ID")
TARGET_URL = "https://www.wanted.co.kr"

# --- [초고화질 및 레이아웃 설정] ---
WEB_VIEWPORT_W = 1920
WEB_RENDER_HEIGHT = 2500
WEB_TARGET_WIDTH = 1100   # WEB(PC) 최종 폭

APP_VIEWPORT_W = 400
APP_VIEWPORT_H = 1000
APP_TARGET_WIDTH = 320    # MOBILE(APP) 최종 폭

LAYOUT_GAP = 80           # WEB / APP 사이 간격 (PDF 내)


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
    """
    두 번째 예시 PDF처럼:
    - 상단: 제목 (파일명 기반)
    - 중간: 좌 WEB, 우 MOBILE
    - 하단: 'WEB(PC)' / 'MOBILE(APP)' 레이블
    """
    try:
        image1 = Image.open(web_img_path).convert('RGB')
        image2 = Image.open(app_img_path).convert('RGB')

        # 페이지 여백 및 레이아웃 설정
        margin_x = 120
        margin_y = 120
        title_gap = 80
        label_gap = 40

        # 페이지 전체 폭/높이 계산
        content_width = image1.width + image2.width + LAYOUT_GAP
        page_width = content_width + margin_x * 2

        content_height = max(image1.height, image2.height)
        # 레이블 영역 조금 추가
        page_height = margin_y + title_gap + content_height + label_gap + 120

        # 흰 배경 페이지 생성
        page = Image.new('RGB', (page_width, page_height), (255, 255, 255))
        draw = ImageDraw.Draw(page)

        # 기본 폰트 (시스템 폰트 경로 지정까지 가면 귀찮아지니 일단 기본)
        font_title = ImageFont.load_default()
        font_label = ImageFont.load_default()

        # 제목: 파일명(확장자 제거) 그대로 사용
        title_text = os.path.splitext(os.path.basename(output_pdf_path))[0]
        draw.text((margin_x, margin_y), title_text, fill=(0, 0, 0), font=font_title)

        # 이미지 배치 위치
        image_top = margin_y + title_gap
        web_left = margin_x
        app_left = margin_x + image1.width + LAYOUT_GAP

        page.paste(image1, (web_left, image_top))
        page.paste(image2, (app_left, image_top))

        # 레이블 위치
        web_label_y = image_top + image1.height + 30
        app_label_y = image_top + image2.height + 30

        draw.text((web_left, web_label_y), "WEB(PC)", fill=(0, 0, 0), font=font_label)
        draw.text((app_left, app_label_y), "MOBILE(APP)", fill=(0, 0, 0), font=font_label)

        # PDF로 저장
        page.save(output_pdf_path, "PDF", resolution=300.0, save_all=True)
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


def hide_small_fixed_banners(page):
    """
    App 화면 하단에 떠 있는 '앱 설치 유도' 같은
    작은 fixed 배너들 제거.
    - bottom 근처
    - 전체 폭보다 충분히 좁은 요소만 제거
    """
    js = """
    () => {
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const nodes = Array.from(document.querySelectorAll('*'));
      for (const el of nodes) {
        const style = window.getComputedStyle(el);
        if (style.position === 'fixed') {
          const rect = el.getBoundingClientRect();
          const isBottom = rect.bottom > vh * 0.5;   // 화면 하단 절반 영역
          const isNarrow = rect.width < vw * 0.9;    // 전체폭보다 좁은 요소만
          const isNotFullHeight = rect.height < vh * 0.8;
          const text = (el.innerText || '').trim();

          if (isBottom && isNarrow && isNotFullHeight) {
            if (
              text.includes('앱으로') ||
              text.toLowerCase().includes('app') ||
              text.includes('원티드 앱')
            ) {
              el.style.display = 'none';
            }
          }
        }
      }
    }
    """
    try:
        page.evaluate(js)
    except Exception as e:
        print(f"⚠️ floating banner 제거 중 오류: {e}")


def get_dynamic_clip_height(page, selector, min_height):
    """지정 selector의 bottom 기준으로 캡쳐 높이 동적 계산"""
    return page.evaluate(f"""() => {{
        const el = document.querySelector("{selector}");
        if (el) {{
            const rect = el.getBoundingClientRect();
            return rect.bottom + window.scrollY + 60; 
        }}
        return {min_height};
    }}""")


# =========================
#  Lazy 포함 배너 포지션 감지용
# =========================
def get_leftmost_banner_id(page):
    """
    현재 뷰포트에 보이는 메인 배너 슬라이드 중
    '가장 왼쪽' 배너의 ID 반환
    """
    js = """
    () => {
        const slides = Array.from(
            document.querySelectorAll("section[class*='BannerArea_MainBannerArea'] li[class*='BannerArea_MainBannerArea__slider__slide']")
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


def capture_web_banner_area(page, image_path):
    """
    Web 메인 배너 영역만 캡쳐.
    section 기준으로 안 잡히면 기존 clip 방식 fallback.
    """
    try:
        container = page.locator("section[class*='BannerArea_MainBannerArea']").first
        if container.count() > 0:
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
        # [Step 1] Web 캡쳐 (PC, 1920px) - lazy 포함 순차 주행
        # ------------------------------------------------------------------
        context_web = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": WEB_VIEWPORT_W, "height": WEB_RENDER_HEIGHT},
            device_scale_factor=2.5  # 3.0 → 2.5로 조정 (선명도+용량 균형)
        )
        page_web = context_web.new_page()

        print(f"🌐 [Web] 접속 중: {TARGET_URL}")
        page_web.goto(TARGET_URL)
        time.sleep(3)
        handle_popup(page_web)

        # 메인 배너 섹션 안에서만 슬라이드/버튼 찾기
        banner_section = page_web.locator("section[class*='BannerArea_MainBannerArea']").first
        try:
            banner_section.locator(
                "li[class*='BannerArea_MainBannerArea__slider__slide']"
            ).first.wait_for(state="visible", timeout=15000)
        except Exception:
            print("❌ 메인 배너 로딩 실패")
            browser.close()
            return

        slides = banner_section.locator("li[class*='BannerArea_MainBannerArea__slider__slide']")
        total_dom_slides = slides.count()
        print(f"📊 총 {total_dom_slides}개의 슬라이드 DOM 발견 (Web)")

        next_btn = banner_section.locator('button[aria-label="다음"]').first

        # 왼쪽 맨 앞 배너 기준으로 순차 캡쳐
        captured_infos = []
        captured_ids = set()
        step = 0
        max_steps = 50  # 안전장치

        while True:
            current_id = get_leftmost_banner_id(page_web)
            print(f"[DEBUG] step {step}, 현재 왼쪽 배너 ID = {current_id}")

            if current_id and current_id not in captured_ids:
                # 새 배너 발견 → 캡쳐
                idx = len(captured_infos)
                web_filename = f"web_{idx}.jpg"
                capture_web_banner_area(page_web, web_filename)
                resize_image_high_quality(web_filename, WEB_TARGET_WIDTH)

                captured_infos.append({"id": current_id, "web_filename": web_filename})
                captured_ids.add(current_id)
                print(f"   ✅ Web 캡쳐 완료: {web_filename} (banner_id={current_id})")

            step += 1
            if step >= max_steps:
                print("[WARN] max_steps에 도달하여 Web 순회를 종료합니다.")
                break

            # '다음' 버튼 상태 확인
            try:
                if not next_btn.is_visible():
                    print("[INFO] '다음' 버튼이 더 이상 보이지 않아 순회를 종료합니다.")
                    break
                if next_btn.is_disabled():
                    print("[INFO] '다음' 버튼이 disabled 상태입니다. 마지막 슬라이드로 판단하고 종료합니다.")
                    break
                next_btn.click()
                page_web.wait_for_timeout(900)  # 애니메이션 + lazy 로딩 대기
            except Exception as e:
                print(f"[ERROR] next 버튼 클릭 실패: {e}")
                break

        print(f"📊 최종 캡쳐된 Web 배너 수: {len(captured_infos)}")
        print("   IDs:", [c['id'] for c in captured_infos])

        if not captured_infos:
            print("❌ 캡쳐된 배너가 없어 App/PDF 단계는 건너뜁니다.")
            browser.close()
            return

        # ------------------------------------------------------------------
        # [Step 2] App 캡쳐 (Mobile) - 하단 플로팅 배너 제거 후 캡쳐
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
        # 하단 앱 설치 배너 등 제거
        hide_small_fixed_banners(page_app)

        for idx, info in enumerate(captured_infos):
            banner_id = info["id"]
            web_filename = info["web_filename"]

            print(f"\n📸 [App] {idx+1}/{len(captured_infos)} - {banner_id} 캡쳐 중...")

            try:
                # App에서도 메인 배너 섹션 기준으로 캡쳐 (특정 배너 정렬은 일단 보류, 상단 영역 통째로)
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
                        initial_comment=f"📢 [{idx+1}/{len(captured_infos)}] {banner_id} 게재 보고"
                    )
                    print(f"   🚀 슬랙 전송 완료")

                # 파일 정리
                for f in (web_filename, app_filename, pdf_filename):
                    if os.path.exists(f):
                        os.remove(f)

            except Exception as e:
                print(f"   ❌ App 처리 중 오류: {e}")

        print("\n✅ 모든 작업 완료!")
        browser.close()


if __name__ == "__main__":
    main()
