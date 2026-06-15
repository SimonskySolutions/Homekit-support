# How to Get All Photos of a Product from Odoo — Integration Guide

This guide explains the script **`odoo_product_images_example.py`**: what it does,
what you need to run it, and how the website developers should use it. It is written
so a non‑technical reader can follow the *what* and the *why*, with clearly marked
sections for the developers who do the actual integration.

---

## 1. In plain English: what is this?

Homekitfit keeps every product's **photos inside Odoo**. Each product has:

- **one main photo**, and
- **any number of additional photos** (a gallery).

This script is the **bridge** that lets the website pull **all photos of a given
product**, in the right order, straight from Odoo. Your developers take it and plug
it into the website so product pages always show the current photos.

> **Where the photos live (important):**
> - The **main photo** is the `image_1920` field on the product (`product.template`).
> - **Every other photo** is a separate record in the **`cloudcart.product.image`**
>   gallery, linked to the product and kept in display order by a `sequence` number.
>
> So **"all photos" = the main image + the gallery records, in `sequence` order.**

---

## 2. What the script does (step by step)

1. **Logs in** to Odoo with an API key.
2. **Finds the product** by its **CloudCart ID**, or its **SKU**, or its **barcode**.
3. **Reads the main photo** (`image_1920`) from the product.
4. **Reads the gallery** (`cloudcart.product.image`) for that product, **ordered**.
5. **Returns/saves every photo in order** — main first, then gallery #2, #3, …

Each photo comes back as **image data** (Base64), which the script decodes and saves
as real `.jpg` / `.png` / `.webp` files. Re‑running it is always safe — it only reads.

---

## 3. What you need before you can use it (requirements)

| # | Requirement | Notes |
|---|-------------|-------|
| 1 | **Python 3** (3.7+) | The script uses only built‑in Python — **nothing to install**. |
| 2 | **Network access** to the Odoo server | Must reach the Odoo address below. |
| 3 | **Odoo login details** (4 values) | Provided by Simonsky Solutions. **Use a dedicated API user**, not a personal password. |
| 4 | **A product identifier** | CloudCart product ID, SKU, or barcode. |

**Connection values**

| Value | What to use |
|-------|-------------|
| URL | `https://simonskysolutions-homekitfit-official.odoo.com` |
| Database | `simonskysolutions-homekitfit-official-main-23810682` |
| User | a dedicated API user (e.g. `website-api`) |
| API key | the API key generated for that user |

---

## 4. How to run it

```bash
export ODOO_USER='website-api'
export ODOO_KEY='<that user's API key>'

# All photos of the product with CloudCart id 1040, full size:
python3 odoo_product_images_example.py --cloudcart-id 1040

# By SKU or barcode instead:
python3 odoo_product_images_example.py --sku ABC123
python3 odoo_product_images_example.py --barcode 3800235261842

# Choose a smaller size and an output folder:
python3 odoo_product_images_example.py --cloudcart-id 1040 --size image_512 --out ./downloaded
```

Example output:

```
Product: [893] Сензор за присъствие Aqara Presence Multi-Sensor FP300
Found 13 photo(s) at size 'image_512'.
  #01     53654 bytes  <- product.template/893        -> ./downloaded/893_01.webp
  #02     55942 bytes  <- cloudcart.product.image/5252 -> ./downloaded/893_02.jpg
  ...
  #13     22374 bytes  <- cloudcart.product.image/6    -> ./downloaded/893_13.png
```

---

## 5. Developer reference (the API calls)

The script talks to Odoo over **JSON‑RPC** (`POST /jsonrpc`, only built‑in Python).
You can use **XML‑RPC** (`/xmlrpc/2/...`) the same way if you prefer.

**Step 1 — authenticate** → get a `uid`:

```
service="common", method="authenticate", args=[db, user, api_key, {}]
```

**Step 2 — find the product template id** (pick one):

```python
# by CloudCart id (stored as text; tolerate a trailing ".0")
product.template.search_read(
    ['|', ('x_studio_cloudcart_id','=','1040'), ('x_studio_cloudcart_id','=','1040.0')],
    fields=['id','name'], limit=1)

# or by SKU
product.template.search_read([('default_code','=','ABC123')], fields=['id','name'], limit=1)

# or by barcode
product.template.search_read([('barcode','=','3800235261842')], fields=['id','name'], limit=1)
```

**Step 3 — read the main photo** from the product:

```python
product.template.read([template_id], ['image_1920'])
```

**Step 4 — read the gallery, in order:**

```python
cloudcart.product.image.search_read(
    [('product_tmpl_id','=', template_id)],
    fields=['image_1920','name','sequence','cloudcart_src'],
    order='sequence, id')
```

**Step 5 — assemble the ordered list:** the main `image_1920` is photo **#1**, then
the gallery records in `sequence` order are #2, #3, … Each `image_*` field is the
photo encoded as **Base64**; decode it to get the raw image bytes.

### Image sizes (Odoo pre‑generates all of them)

| Field | Use for |
|-------|---------|
| `image_1920` | full size (default) |
| `image_1024` | large |
| `image_512` | medium / product page |
| `image_256` | thumbnail |
| `image_128` | small thumbnail |

Both the product's main image **and** every gallery record expose all five sizes, so
you can request whichever you need — e.g. `image_512` for the page, `image_128` for a
strip of thumbnails.

### Useful fields on `cloudcart.product.image`

| Field | Meaning |
|-------|---------|
| `product_tmpl_id` | the product this photo belongs to |
| `sequence` | display order (lower = earlier) |
| `image_1920` … `image_128` | the photo, in each size (Base64) |
| `name` | a label for the photo |
| `cloudcart_src` | original CloudCart source URL (provenance only) |

---

## 6. Important notes

> **The photos are served via the API as image data (Base64), not as public URLs.**
> A direct link like `/web/image/...` will **not** work for anonymous website
> visitors — Odoo returns a placeholder, because these images are not published to a
> public website. The correct pattern is: your website backend **pulls the images
> through the API** (as shown here) and **caches/serves them from your own side**
> (your CDN, storage, or `<img>` host). Fetch once and cache; don't call Odoo on
> every page view.

- **Use a dedicated API user** with read access — never embed a personal password.
- **Image formats vary** (`.jpg`, `.png`, `.webp`); detect from the bytes (the script does).
- **Ordering matters** — always sort the gallery by `sequence` so photos appear as intended.
- **Read‑only & safe** — this never writes to Odoo; re‑run as often as needed.
