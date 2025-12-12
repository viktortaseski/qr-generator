# generator_scaleddatabase.py
import os
import secrets
import string
from pathlib import Path
from urllib.parse import urlencode

import psycopg2
import qrcode
from dotenv import load_dotenv
from PIL import Image, ImageDraw
from qrcode.constants import ERROR_CORRECT_H
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.colormasks import SolidFillColorMask
from qrcode.image.styles.moduledrawers import CircleModuleDrawer

# -------------------- CONFIG --------------------
# Base landing page (should not include query params)
BASE_URL = "https://selfservscaled.onrender.com/"
OUTPUT_DIR = "qr-codes-scaled"

LOGO_PATH = os.path.expanduser("~/Desktop/logo.png")

START_INDEX = 1
COUNT = 10

QR_BOX_SIZE = 12
QR_BORDER = 4
LOGO_SCALE = 0.20

ADD_WHITE_PAD = True
WHITE_PAD_RATIO = 1.15
PAD_ALPHA = 255
PAD_ROUNDED = True

RESTAURANT_ID = 2  # adjust to the target restaurant ID in scaleddatabase

ENV_PATH = Path(__file__).resolve().parent / ".env"

load_dotenv(ENV_PATH)
DB_CFG = {
    "host": os.getenv("PGHOST"),
    "port": os.getenv("PGPORT", "5432"),
    "dbname": os.getenv("PGDATABASE"),
    "user": os.getenv("PGUSER"),
    "password": os.getenv("PGPASSWORD"),
    "sslmode": os.getenv("PGSSLMODE", "require"),
}
# ------------------------------------------------


def secure_token(n: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def load_logo_or_none(path: str):
    try:
        if not path or not os.path.exists(path):
            return None
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


def make_qr_with_center_logo(data: str, logo_img: Image.Image | None) -> Image.Image:
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

    logo = logo_img.copy()
    target_logo_w = int(qr_w * LOGO_SCALE)
    aspect = logo.height / max(1, logo.width)
    target_logo_h = int(target_logo_w * aspect)
    logo = logo.resize((max(1, target_logo_w), max(1, target_logo_h)), Image.LANCZOS)

    if ADD_WHITE_PAD:
        pad_w = int(target_logo_w * WHITE_PAD_RATIO)
        pad_h = int(target_logo_h * WHITE_PAD_RATIO)
        pad_w = max(pad_w, target_logo_w + 2)
        pad_h = max(pad_h, target_logo_h + 2)

        composed = Image.new("RGBA", (pad_w, pad_h), (255, 255, 255, 0))

        mask = Image.new("L", (pad_w, pad_h), 0)
        draw = ImageDraw.Draw(mask)
        if PAD_ROUNDED:
            radius = max(8, min(pad_w, pad_h) // 6)
            draw.rounded_rectangle([0, 0, pad_w, pad_h], radius=radius, fill=255)
        else:
            draw.rectangle([0, 0, pad_w, pad_h], fill=255)

        white_rect = Image.new("RGBA", (pad_w, pad_h), (255, 255, 255, PAD_ALPHA))
        composed.paste(white_rect, (0, 0), mask)

        lx = (pad_w - target_logo_w) // 2
        ly = (pad_h - target_logo_h) // 2
        composed.paste(logo, (lx, ly), logo)
        logo = composed
        target_logo_w, target_logo_h = logo.size

    x = (qr_w - target_logo_w) // 2
    y = (qr_h - target_logo_h) // 2
    qr_img.paste(logo, (x, y), logo)

    return qr_img


def ensure_table_and_token(cur, restaurant_id: int, table_name: str) -> tuple[int, str]:
    cur.execute(
        """
        SELECT id, token
        FROM restaurant_tables
        WHERE restaurant_id = %s AND name = %s
        """,
        (restaurant_id, table_name),
    )
    row = cur.fetchone()

    if row:
        table_id, token = row
        if token and token.strip():
            return table_id, token
        new_token = secure_token(16)
        cur.execute(
            "UPDATE restaurant_tables SET token = %s WHERE id = %s",
            (new_token, table_id),
        )
        return table_id, new_token

    new_token = secure_token(16)
    cur.execute(
        """
        INSERT INTO restaurant_tables (restaurant_id, name, token)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (restaurant_id, table_name, new_token),
    )
    table_id = cur.fetchone()[0]
    return table_id, new_token


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    conn = psycopg2.connect(**DB_CFG)
    conn.autocommit = False
    cur = conn.cursor()

    logo_img = load_logo_or_none(LOGO_PATH)

    try:
        for i in range(START_INDEX, START_INDEX + COUNT):
            table_name = f"Table{i:02d}"
            table_id, token = ensure_table_and_token(cur, RESTAURANT_ID, table_name)
            query = urlencode(
                {
                    "restaurant_id": RESTAURANT_ID,
                    "token": token,
                }
            )
            joiner = "&" if "?" in BASE_URL else "?"
            base_url = BASE_URL.rstrip("&?")
            url = f"{base_url}{joiner}{query}"

            qr_img = make_qr_with_center_logo(url, logo_img)

            png_path = os.path.join(OUTPUT_DIR, f"{table_name}.png")
            qr_img.save(png_path)

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
