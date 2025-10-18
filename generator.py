# generator.py
import os
import qrcode
from qrcode.constants import ERROR_CORRECT_H
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers import CircleModuleDrawer
from qrcode.image.styles.colormasks import SolidFillColorMask
from PIL import Image, ImageDraw
import psycopg2
import secrets
import string

# -------------------- CONFIG --------------------
# This URL is what your *printed* QR encodes permanently (per-table token).
# The backend will issue/validate *short-lived* session tokens when the app loads.
BASE_URL = "https://selfserv-web.onrender.com/?token="
OUTPUT_DIR = "qr-codes"

# Optional center logo (safe to leave blank / non-existent)
LOGO_PATH = os.path.expanduser("~/Desktop/logo.png")

# Tables to (ensure and) generate:
START_INDEX = 1  # table00, table01, ...
COUNT = 20  # how many tables from START_INDEX

# QR appearance
QR_BOX_SIZE = 12  # pixel size of each QR module
QR_BORDER = 4  # quiet zone (modules) - keep >= 4
LOGO_SCALE = 0.20  # logo width as fraction of QR width (0.15–0.25 is safe)

# Pad behind logo (for contrast)
ADD_WHITE_PAD = True
WHITE_PAD_RATIO = 1.15  # how much larger the pad is vs logo
PAD_ALPHA = 255  # fully opaque: avoids gray outline/halo
PAD_ROUNDED = True

# DB connection (your existing Render PG)
DB_CFG = {
    "host": "dpg-d2rbes7diees73e53dvg-a.oregon-postgres.render.com",
    "port": "5432",
    "dbname": "selfservdb",
    "user": "selfservdb_user",
    "password": "CvAiRRsrnXXSoqjVquAf4J5OkM6mw4kd",
    "sslmode": "require",
}
# ------------------------------------------------


def secure_token(n: int = 16) -> str:
    """Generate a URL-safe, mixed-case + digits token of length n."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def load_logo_or_none(path: str):
    """Try to load a logo; return PIL Image or None if missing."""
    try:
        if not path or not os.path.exists(path):
            return None
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


def make_qr_with_center_logo(data: str, logo_img: Image.Image | None) -> Image.Image:
    """
    Build a high-redundancy QR with circular modules and (optionally) place a centered logo.
    Returns a PIL Image (RGBA).
    """
    # 1) Build the QR (circular modules)
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_H,
        box_size=QR_BOX_SIZE,
        border=QR_BORDER,
    )
    qr.add_data(data)
    qr.make(fit=True)

    qr_img = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=CircleModuleDrawer(),
        color_mask=SolidFillColorMask(
            front_color=(0, 0, 0), back_color=(255, 255, 255)
        ),
    ).convert("RGBA")

    if logo_img is None:
        return qr_img

    qr_w, qr_h = qr_img.size

    # 2) Resize the logo
    logo = logo_img.copy()
    target_logo_w = int(qr_w * LOGO_SCALE)
    aspect = logo.height / max(1, logo.width)
    target_logo_h = int(target_logo_w * aspect)
    logo = logo.resize((max(1, target_logo_w), max(1, target_logo_h)), Image.LANCZOS)

    # 3) Optional white pad behind logo
    if ADD_WHITE_PAD:
        pad_w = int(target_logo_w * WHITE_PAD_RATIO)
        pad_h = int(target_logo_h * WHITE_PAD_RATIO)
        pad_w = max(pad_w, target_logo_w + 2)
        pad_h = max(pad_h, target_logo_h + 2)

        composed = Image.new("RGBA", (pad_w, pad_h), (255, 255, 255, 0))

        # Create mask for rounded/square pad
        mask = Image.new("L", (pad_w, pad_h), 0)
        draw = ImageDraw.Draw(mask)
        if PAD_ROUNDED:
            radius = max(8, min(pad_w, pad_h) // 6)
            draw.rounded_rectangle([0, 0, pad_w, pad_h], radius=radius, fill=255)
        else:
            draw.rectangle([0, 0, pad_w, pad_h], fill=255)

        # Solid white pad
        white_rect = Image.new("RGBA", (pad_w, pad_h), (255, 255, 255, PAD_ALPHA))
        composed.paste(white_rect, (0, 0), mask)

        # Center logo on pad
        lx = (pad_w - target_logo_w) // 2
        ly = (pad_h - target_logo_h) // 2
        composed.paste(logo, (lx, ly), logo)
        logo = composed
        target_logo_w, target_logo_h = logo.size

    # 4) Paste at center of QR
    x = (qr_w - target_logo_w) // 2
    y = (qr_h - target_logo_h) // 2
    qr_img.paste(logo, (x, y), logo)

    return qr_img


def ensure_table_and_token(cur, table_name: str) -> tuple[int, str]:
    """
    Ensure a row exists for table_name in restaurant_tables.
    - If it exists and has a token, keep it (stable).
    - If it exists but token is NULL/empty, set a new token.
    - If it doesn't exist, create with a new token.
    Returns (table_id, permanent_token).
    """
    cur.execute(
        "SELECT id, token FROM restaurant_tables WHERE name = %s", (table_name,)
    )
    row = cur.fetchone()

    if row:
        table_id, token = row
        if token and token.strip():
            return table_id, token  # keep existing
        # set new token if missing
        new_token = secure_token(16)
        cur.execute(
            "UPDATE restaurant_tables SET token = %s WHERE id = %s",
            (new_token, table_id),
        )
        return table_id, new_token

    # insert new row
    new_token = secure_token(16)
    cur.execute(
        """
        INSERT INTO restaurant_tables (name, token)
        VALUES (%s, %s)
        RETURNING id
        """,
        (table_name, new_token),
    )
    table_id = cur.fetchone()[0]
    return table_id, new_token


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Connect DB
    conn = psycopg2.connect(**DB_CFG)
    conn.autocommit = False
    cur = conn.cursor()

    logo_img = load_logo_or_none(LOGO_PATH)

    try:
        for i in range(START_INDEX, START_INDEX + COUNT):
            table_name = f"table{i:02d}"

            # Ensure row exists and obtain the (stable) permanent token
            table_id, token = ensure_table_and_token(cur, table_name)
            url = f"{BASE_URL}{token}"

            # Build QR with (optional) centered logo
            qr_img = make_qr_with_center_logo(url, logo_img)

            # Save PNG
            png_path = os.path.join(OUTPUT_DIR, f"{table_name}.png")
            qr_img.save(png_path)

            # Update url + qr_code_path (token is intentionally NOT changed here)
            cur.execute(
                """
                UPDATE restaurant_tables
                SET url = %s,
                    qr_code_path = %s
                WHERE id = %s
                """,
                (url, png_path, table_id),
            )

            print(f"✅ {table_name}: token={token}  →  {url}")
            print(f"   saved: {png_path}")

        conn.commit()
        print("🎉 All done.")
    except Exception as e:
        conn.rollback()
        print("❌ Error, rolled back:", e)
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
