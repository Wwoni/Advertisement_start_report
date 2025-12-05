import os
import time
from datetime import datetime
from typing import List, Dict, Any

from playwright.sync_api import sync_playwright
from PIL import Image
from slack_sdk import WebClient

# --- 환경 변수 ---
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL_ID")
TARGET_URL = "https://www.wanted.co.kr"

# --- 캡쳐 해상도 / 레이아웃 설정 ---
# "브라우저에서 보이는 화면 그대로" 기준
WEB_WIDTH = 1100
WEB_HEIGHT = 900   # 하단 배너까지 보이도록 넉넉히

APP_WIDTH = 353
APP_HEIGHT = 780   # 모바일 한 화면 기준

GAP = 40  # Web / App 사이 간격(px)


def get_banner_id(href: str) -> str:
    """href → 배너 ID 추출"""
    if not href:
        return "unknown"
    clean = href.split("?")[0]
    segs = [s for s in clean.split("/") if s]
    if not segs:
        return "unknown"
    return segs[-1]


def get_unique_banners(page, slider_selector: str) -> List[Dict[str, Any]]:
    """
    슬라이더 안의 고유 배너 목록을 왼쪽→오른쪽 순으로 반환.
    각 항목: {id, href, offset}
    """
    banners = page.evaluate(
        """(selector) => {
            const ul = document.querySelector(selector);
            if (!ul) return [];
            const items = Array.from(
              ul.querySelectorAll("li[class*='BannerArea_MainBannerArea__slider__slide']")
            );
            const seen = new Set();
            const result = [];
            for (const li of items) {
                const a = li.querySelector("a[href]");
                if (!a) continue;
                const href = a.getAttribute("href");
                if (!href) continue;
                const clean = href.split("?")[0];
                const segs = clean.split("/").filter(Boolean);
                const id = segs.length ? segs[segs.length - 1] : "unknown";
                if (seen.has(id)) continue;
                seen.add(id);
                result.push({
                    id,
                    href,
                    offset: li.offsetLeft
                });
            }
            result.sort((a, b) => a.offset - b.offset);
            return result;
        }""",
        slider_selector,
    )
    return banners or []


def move_slider_to_offset(page, slider_selector: str, offset: float):
    """슬라이더를 강제로 특정 offset까지 translate3d로 이동"""
    page.evaluate(
        """(params) => {
            const { selector, off } = params;
            const ul = document.querySelector(selector);
            if (!ul) return;
            ul.style.transition = 'none';
            ul.style.transform = `translate3d(${-off}px, 0, 0)`;
        }""",
        {"selector": slider_selector, "off": float(offset)},
    )
    # lazy 이미지 로딩 여유
    time.sleep(0.7)


def handle_desktop_popup(page):
    """PC용 메인 진입 시 팝업 닫기 (ESC + 닫기 버튼 시도)"""
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
    except Exception:
        pass

    try:
        close_btn = page.locator(
            "button[aria-label*='닫기'], button[class*='close'], "
            "div[id*='Modal'] button, div[class*='Modal'] button"
        ).first
        if close_btn.is_visible():
            close_btn.click()
            time.sleep(0.7)
    except Exception:
        pass


def handle_app_popup(page):
    """모바일 앱 하단 '앱 설치' 모달 닫기"""
    try:
        page.wait_for_timeout(1500)
        popup_close = page.locator(
            "div.AppInstallPopup_modal_wrapper__VLXRm "
            "button.AppInstallPopup_modal_contents__closeButton__1nsi_, "
            "div.AppInstallPopup_modal_wrapper__VLXRm button[aria-label='닫기'], "
            "div.AppInstallPopup_modal_wrapper__VLXRm button[data-type='closeToday']"
        ).first
        if popup_close.is_visible():
            popup_close.click()
            page.wait_for_timeout(500)
    except Exception:
        pass


def capture_web_banners(page, banners: List[Dict[str, Any]], out_dir: str) -> List[Dict[str, Any]]:
    """PC 웹 배너 전체 캡쳐 → viewport 전체 캡쳐"""
    slider_selector = "ul[class*='BannerArea_MainBannerArea__slider']"
    results = []

    if not banners:
        print("❌ Web 배너 목록이 비어 있습니다.")
        return results

    view_size = page.viewport_size
    clip_h = view_size["height"]

    print(f"📊 Web 배너 {len(banners)}개 캡쳐 시도 (고유 ID 기준)")

    for idx, b in enumerate(banners):
        banner_id = get_banner_id(b.get("href"))
        offset = b.get("offset", 0)
        print(f"[WEB] {idx+1}/{len(banners)} - {banner_id} (offset={offset})")

        # 해당 배너가 왼쪽에 오도록 슬라이더 이동
        move_slider_to_offset(page, slider_selector, offset)

        filename = os.path.join(out_dir, f"web_{idx}_{banner_id}.png")

        page.screenshot(
            path=filename,
            clip={"x": 0, "y": 0, "width": WEB_WIDTH, "height": clip_h},
            type="png",          # PNG로 고해상도 유지
            full_page=False,     # viewport 영역만
            # scale 기본값 'device' → device_scale_factor 반영 (2배 해상도)
        )
        print(f"   ✅ Web 캡쳐 완료: {filename}")
        results.append({"id": banner_id, "path": filename})
    return results


def capture_app_banners(page, web_banners: List[Dict[str, Any]], out_dir: str) -> List[Dict[str, Any]]:
    """모바일 App 배너를 Web 순서에 맞춰 캡쳐 (viewport 전체)"""
    slider_selector = "ul[class*='BannerArea_MainBannerArea__slider']"

    app_banners = get_unique_banners(page, slider_selector)
    if not app_banners:
        print("❌ App 배너 목록을 찾지 못했습니다.")
        return []

    app_index = {get_banner_id(b["href"]): b for b in app_banners}
    results = []

    view_size = page.viewport_size
    clip_h = view_size["height"]

    print(f"📊 App 배너 고유 {len(app_index)}개 (DOM 기준 {len(app_banners)}개)")

    for idx, wb in enumerate(web_banners):
        banner_id = wb["id"]
        app_info = app_index.get(banner_id)
        print(f"[APP] {idx+1}/{len(web_banners)} - {banner_id} 위치 맞추는 중...")

        if not app_info:
            print(f"   ⚠️ App 쪽에서 동일 ID({banner_id})를 찾지 못해 스킵")
            continue

        move_slider_to_offset(page, slider_selector, app_info.get("offset", 0))

        filename = os.path.join(out_dir, f"app_{idx}_{banner_id}.png")

        page.screenshot(
            path=filename,
            clip={"x": 0, "y": 0, "width": APP_WIDTH, "height": clip_h},
            type="png",
            full_page=False,
        )
        print(f"   ✅ App 캡쳐 완료: {filename}")
        results.append({"id": banner_id, "path": filename})

    return results


def create_pdf_pairs(web_caps, app_caps, out_dir: str) -> None:
    """
    Web / App 짝이 맞는 것만 골라 PDF 생성.
    - 상단 title 텍스트는 넣지 않음
    - PNG 원본 그대로 PDF에 삽입 (리사이즈 X)
    """
    client = WebClient(token=SLACK_TOKEN) if SLACK_TOKEN else None
    today_prefix = datetime.now().strftime("%y%m%d")

    web_map = {w["id"]: w["path"] for w in web_caps}
    app_map = {a["id"]: a["path"] for a in app_caps}

    common_ids = [bid for bid in web_map.keys() if bid in app_map]
    print(f"📊 PDF로 만들 공통 배너 수: {len(common_ids)}")

    for idx, bid in enumerate(common_ids):
        web_path = web_map[bid]
        app_path = app_map[bid]

        web_img = Image.open(web_path).convert("RGB")
        app_img = Image.open(app_path).convert("RGB")

        canvas_height = max(web_img.height, app_img.height)
        canvas_width = web_img.width + GAP + app_img.width

        canvas = Image.new("RGB", (canvas_width, canvas_height), (255, 255, 255))
        canvas.paste(web_img, (0, 0))
        canvas.paste(app_img, (web_img.width + GAP, 0))

        pdf_name = f"{today_prefix}_{bid}_게재보고.pdf"
        pdf_path = os.path.join(out_dir, pdf_name)

        # PNG 고해상도 그대로 PDF에 넣기 (별도 DPI 조정 없음)
        canvas.save(pdf_path, "PDF")

        print(f"📄 PDF 생성 완료: {pdf_path}")

        if client and SLACK_CHANNEL:
            client.files_upload_v2(
                channel=SLACK_CHANNEL,
                file=pdf_path,
                title=pdf_name,
                initial_comment=f"📢 [{idx+1}/{len(common_ids)}] {bid} 게재 보고",
            )
            print("   🚀 슬랙 전송 완료")


def main():
    out_dir = "."
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ---------------- Web (PC) ----------------
        context_web = browser.new_context(
            viewport={"width": WEB_WIDTH, "height": WEB_HEIGHT},
            device_scale_factor=2.0,   # Retina 비슷한 2배 해상도
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page_web = context_web.new_page()

        print("🚀 브라우저 실행 (고화질 모드)...")
        print(f"🌐 [Web] 접속 중: {TARGET_URL}")
        page_web.goto(TARGET_URL, wait_until="networkidle")
        time.sleep(2)
        handle_desktop_popup(page_web)

        slider_selector = "ul[class*='BannerArea_MainBannerArea__slider']"
        page_web.wait_for_selector(slider_selector, timeout=15000)

        web_banners = get_unique_banners(page_web, slider_selector)
        if not web_banners:
            print("❌ Web 배너 로딩 실패")
            browser.close()
            return

        print(f"📊 Web DOM 기준 배너 수: {len(web_banners)}")
        web_caps = capture_web_banners(page_web, web_banners, out_dir)

        # ---------------- App (Mobile) ----------------
        context_app = browser.new_context(
            viewport={"width": APP_WIDTH, "height": APP_HEIGHT},
            device_scale_factor=2.0,
            is_mobile=True,
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/16.0 Mobile/15E148 Safari/604.1"
            ),
        )
        page_app = context_app.new_page()

        print("\n🌐 [App] 접속 중...")
        page_app.goto(TARGET_URL, wait_until="networkidle")
        time.sleep(2)
        handle_app_popup(page_app)
        page_app.wait_for_selector(slider_selector, timeout=15000)

        app_caps = capture_app_banners(page_app, web_caps, out_dir)

        # ---------------- PDF 생성 & 슬랙 전송 ----------------
        create_pdf_pairs(web_caps, app_caps, out_dir)

        browser.close()
        print("\n✅ 모든 작업 완료!")


if __name__ == "__main__":
    main()
