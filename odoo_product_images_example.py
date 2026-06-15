#!/usr/bin/env python3
"""
odoo_product_images_example.py — Fetch ALL photos of a product from Odoo.

Homekitfit stores every product's photos inside Odoo:

    * the MAIN photo  -> field  image_1920  on  product.template
    * every OTHER photo -> one record each in the  cloudcart.product.image
      gallery, linked to the product and ordered by the `sequence` field.

So "all photos for a product" = the main image  +  the gallery records,
in order. This script pulls them and saves them to a folder, and is meant
as a copy-paste starting point for the website developers.

It uses Odoo's JSON-RPC API over HTTPS and ONLY built-in Python — there is
nothing to pip-install.

------------------------------------------------------------------------
USAGE
------------------------------------------------------------------------
    export ODOO_USER='website-api'          # a dedicated API user
    export ODOO_KEY='<that user's API key>'

    # by CloudCart product id:
    python3 odoo_product_images_example.py --cloudcart-id 1040

    # or by SKU / barcode:
    python3 odoo_product_images_example.py --sku ABC123
    python3 odoo_product_images_example.py --barcode 3800235261842

    # choose a size and an output folder:
    python3 odoo_product_images_example.py --cloudcart-id 1040 \
            --size image_512 --out ./downloaded

Image size options (Odoo pre-generates all of them):
    image_1920 (default, full)  image_1024  image_512  image_256  image_128
------------------------------------------------------------------------
"""

import argparse
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

# === Defaults (provided by Simonsky Solutions) ========================
DEFAULT_URL = "https://simonskysolutions-homekitfit-official.odoo.com"
DEFAULT_DB = "simonskysolutions-homekitfit-official-main-23810682"

VALID_SIZES = ("image_1920", "image_1024", "image_512", "image_256", "image_128")


# === Minimal JSON-RPC client (built-in Python only) ===================
class Odoo:
    def __init__(self, url, db, user, key):
        self.url = url.rstrip("/")
        self.db, self.user, self.key = db, user, key
        self.uid = None
        self.ctx = ssl.create_default_context()

    def _call(self, service, method, args):
        payload = {"jsonrpc": "2.0", "method": "call",
                   "params": {"service": service, "method": method, "args": args},
                   "id": 0}
        req = urllib.request.Request(
            f"{self.url}/jsonrpc",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120, context=self.ctx) as r:
                body = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:300]}")
        if "error" in body:
            raise RuntimeError(f"Odoo error: {json.dumps(body['error'])[:600]}")
        return body.get("result")

    def login(self):
        self.uid = self._call("common", "authenticate",
                              [self.db, self.user, self.key, {}])
        if not self.uid:
            raise SystemExit("Authentication failed — check ODOO_USER / ODOO_KEY / db.")
        return self.uid

    def execute_kw(self, model, method, args, kw=None):
        return self._call("object", "execute_kw",
                          [self.db, self.uid, self.key, model, method, args, kw or {}])


# === Helpers ==========================================================
def find_template_id(odoo, cloudcart_id=None, sku=None, barcode=None):
    """Return the product.template id for the given identifier (or None)."""
    if cloudcart_id:
        # CloudCart ids are stored as text; tolerate a trailing ".0".
        domain = ["|", ("x_studio_cloudcart_id", "=", str(cloudcart_id)),
                  ("x_studio_cloudcart_id", "=", f"{cloudcart_id}.0")]
    elif sku:
        domain = [("default_code", "=", sku)]
    elif barcode:
        domain = [("barcode", "=", barcode)]
    else:
        raise SystemExit("Provide one of --cloudcart-id / --sku / --barcode.")
    res = odoo.execute_kw("product.template", "search_read",
                          [domain], {"fields": ["id", "name"], "limit": 1,
                                     "context": {"active_test": False}})
    return res[0] if res else None


def get_all_images(odoo, template_id, size="image_1920"):
    """Return an ordered list of dicts: the main image first, then the gallery.

    Each item: {"position", "source", "record_id", "name", "b64"}.
    """
    out = []
    # 1) main image lives on the product itself
    tmpl = odoo.execute_kw("product.template", "read",
                           [[template_id], [size, "name"]])[0]
    if tmpl.get(size):
        out.append({"position": 1, "source": "product.template",
                    "record_id": template_id, "name": tmpl["name"], "b64": tmpl[size]})
    # 2) gallery images, ordered by `sequence`
    gallery = odoo.execute_kw(
        "cloudcart.product.image", "search_read",
        [[("product_tmpl_id", "=", template_id)]],
        {"fields": [size, "name", "sequence"], "order": "sequence, id"})
    for i, g in enumerate(gallery, start=len(out) + 1):
        if g.get(size):
            out.append({"position": i, "source": "cloudcart.product.image",
                        "record_id": g["id"], "name": g.get("name") or "", "b64": g[size]})
    return out


def ext_for(data: bytes) -> str:
    if data[:2] == b"\xff\xd8":
        return "jpg"
    if data[:8].hex().startswith("89504e47"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "img"


# === Main =============================================================
def main():
    ap = argparse.ArgumentParser(description="Download all photos of an Odoo product.")
    ap.add_argument("--cloudcart-id")
    ap.add_argument("--sku")
    ap.add_argument("--barcode")
    ap.add_argument("--size", default="image_1920", choices=VALID_SIZES)
    ap.add_argument("--out", default="./product_images")
    ap.add_argument("--url", default=os.environ.get("ODOO_URL", DEFAULT_URL))
    ap.add_argument("--db", default=os.environ.get("ODOO_DB", DEFAULT_DB))
    ap.add_argument("--user", default=os.environ.get("ODOO_USER"))
    ap.add_argument("--key", default=os.environ.get("ODOO_KEY"))
    args = ap.parse_args()

    if not args.user or not args.key:
        raise SystemExit("Set ODOO_USER and ODOO_KEY (use a dedicated API user).")

    odoo = Odoo(args.url, args.db, args.user, args.key)
    odoo.login()

    tmpl = find_template_id(odoo, args.cloudcart_id, args.sku, args.barcode)
    if not tmpl:
        raise SystemExit("Product not found for the given identifier.")
    tid, tname = tmpl["id"], tmpl["name"]
    print(f"Product: [{tid}] {tname}")

    images = get_all_images(odoo, tid, size=args.size)
    print(f"Found {len(images)} photo(s) at size '{args.size}'.")

    os.makedirs(args.out, exist_ok=True)
    for item in images:
        data = base64.b64decode(item["b64"])
        fname = f"{tid}_{item['position']:02d}.{ext_for(data)}"
        path = os.path.join(args.out, fname)
        with open(path, "wb") as f:
            f.write(data)
        print(f"  #{item['position']:02d}  {len(data):>8} bytes  <- {item['source']}"
              f"/{item['record_id']}  -> {path}")

    print(f"\nDone. {len(images)} file(s) written to {args.out}/")


if __name__ == "__main__":
    main()
