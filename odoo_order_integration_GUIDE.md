# How to Send Orders into Odoo — Integration Guide

This guide explains the script **`odoo_order_integration_example.py`**: what it does,
what you need to run it, and how to use it. It is written so that a non‑technical
reader can follow the *what* and the *why*, with clearly marked sections for the
developers who will do the actual setup.

---

## 1. In plain English: what is this?

Homekitfit runs its business inside **Odoo** (an all‑in‑one business system that
holds customers, products, orders, and invoices).

When a customer buys something on the **website**, that order needs to appear
**inside Odoo** so the Homekitfit team can pack it, ship it, and invoice it.

This script is the **bridge**. It takes one order from the website and writes it
into Odoo as a proper sales order — creating the customer, matching the products,
and setting the order’s status. It is an **example**: your developers take it and
plug it into the live website so this happens automatically for every order.

Think of it as a translator that speaks "website" on one side and "Odoo" on the
other.

---

## 2. What the script does (step by step)

For each order it receives, it:

1. **Finds or creates the customer** in Odoo (matched by email, so the same person
   is never duplicated).
2. **Handles business (B2B) orders** — if the order has company details, it finds
   or creates the company (matched by its VAT / EIK·Bulstat number) and bills the
   invoice to the company instead of the person.
3. **Matches each product** on the order to the matching product in Odoo, using the
   product’s **SKU** first and **barcode** second.
4. **Creates the sales order** in Odoo — or, if that order already exists, **updates
   it** instead of making a duplicate.
5. **Sets the order’s status** (e.g. confirmed, cancelled, quotation sent) to match
   the website.
6. **Optionally creates and posts an invoice** once the order is confirmed/paid.

> **Important safety feature — it can’t create duplicates.**
> The script tags every order with the website’s own order number. If the website
> sends the same order twice (which happens — retries, network hiccups), the script
> recognises it and updates the existing order instead of creating a second one.
> Running it again with the same order is always safe.

---

## 3. What you need before you can use it (requirements)

| # | Requirement | Notes |
|---|-------------|-------|
| 1 | **Python 3** installed (version 3.7 or newer) | The script uses only built‑in Python features — **nothing to install/download** beyond Python itself. |
| 2 | **Network access** to the Odoo server | The machine running the script must be able to reach the Odoo website address. |
| 3 | **Odoo login details** (4 values, see below) | Provided by the Homekitfit / Simonsky Solutions team. Use a dedicated API key, not a personal password. |
| 4 | **Products already exist in Odoo** | The script **matches** products; it does **not** create them. Every product you sell on the website must already be in Odoo with the **same SKU or barcode**. |

### The 4 login details

These are **secrets** — treat them like a password. They are:

| Setting | What it is | Example |
|---------|-----------|---------|
| `ODOO_URL` | The web address of the Odoo system | `https://...staging-....dev.odoo.com` |
| `ODOO_DB` | The database name inside Odoo | `simonskysolutions-homekitfit-...` |
| `ODOO_USERNAME` | The login user | `admin` or an integration user |
| `ODOO_PASSWORD` | The password **or API key** for that user | a long random key |

> 🔐 **Ask the Homekitfit / Simonsky Solutions team for these values.**
> Do **not** type them directly into the script file. Provide them as
> "environment variables" instead (shown below) so they never get saved into the
> code or shared by accident.

---

## 4. How to set it up (for developers)

The script reads its login details from **environment variables**, so you don’t edit
the file. Set them in your terminal before running.

**Windows (PowerShell):**
```powershell
$env:ODOO_URL      = "https://YOUR-ODOO-ADDRESS"
$env:ODOO_DB       = "YOUR-DATABASE-NAME"
$env:ODOO_USERNAME = "admin"
$env:ODOO_PASSWORD = "YOUR-API-KEY"
# Only if Odoo is multi-company and orders belong to a specific company:
# $env:ODOO_COMPANY_ID = "1"

python odoo_order_integration_example.py
```

**Mac / Linux (Terminal):**
```bash
export ODOO_URL="https://YOUR-ODOO-ADDRESS"
export ODOO_DB="YOUR-DATABASE-NAME"
export ODOO_USERNAME="admin"
export ODOO_PASSWORD="YOUR-API-KEY"
# export ODOO_COMPANY_ID="1"   # optional, multi-company only

python3 odoo_order_integration_example.py
```

Running it as‑is executes a **built‑in demo** (the `_demo()` function at the bottom)
that creates one sample order. Use this to confirm the connection works, **then
replace the demo data with real orders from your website**.

> ✅ **Always test against the STAGING Odoo first**, never the live/production system.
> A test order should appear in Odoo under **Sales → Orders**.

---

## 5. The order format your website must provide

The website hands the script one order as a simple structure. Here is what each
piece means:

```python
order = {
    "website_order_id": "100245",     # REQUIRED. Your unique order number.
    "order_ref":        "WEB-100245", # Optional. Human-friendly reference for staff.
    "status":           "paid",       # The website status (see status list below).
    "currency_code":    "EUR",        # Optional. Currency ISO code.

    "customer": {                     # The person who ordered.
        "name":  "Vladimir Velinov",
        "email": "buyer@example.com", # Used to avoid duplicate customers.
        "phone": "+359...",
        "street": "ул. ...", "city": "София", "zip": "1612",
        "country_code": "BG",
    },

    "company": {                      # Optional. Include ONLY for business (B2B) orders.
        "name": "Вилюжън ЕООД",       # Set "company": None for normal B2C orders.
        "vat":  "207266621",          # Bulgarian EIK/Bulstat — used to match the company.
        "street": "...", "city": "...", "zip": "...", "country_code": "BG",
    },

    "lines": [                        # One entry per product in the order.
        {"sku": "SHELLY-PLUS-1", "name": "Shelly Plus 1",
         "qty": 2, "price_unit": 18.50, "discount_pct": 0.0},
        {"sku": "HUE-BLOOM", "name": "Philips Hue Bloom",
         "qty": 1, "price_unit": 79.90, "discount_pct": 10.0},
    ],
}
```

**Field meaning at a glance:**

- **website_order_id** — the most important field. It uniquely identifies the order
  and is what prevents duplicates. Must stay the same if you re‑send the order.
- **status** — where the order is in its lifecycle (see the next section).
- **customer** — the buyer; matched by email.
- **company** — only for B2B; matched by VAT/EIK. Leave it out (or `None`) for
  ordinary consumer orders.
- **lines** — the products. Each needs a **sku** (or **barcode**) that already
  exists in Odoo, a quantity, a unit price, and an optional percentage discount.

> ⚠️ **If a product’s SKU/barcode is not found in Odoo, that line is skipped** and a
> warning is printed. This is why keeping SKUs identical on both sides matters.

---

## 6. Order status — how website statuses map to Odoo

The website’s status word is translated into an Odoo state automatically:

| Website status | Becomes in Odoo |
|----------------|-----------------|
| pending, authorized | Quotation (awaiting confirmation), to invoice |
| processing, shipped | Confirmed sale, to invoice |
| paid, completed, fulfilled, returned | Confirmed sale, invoiced |
| cancelled, declined, failed, voided, refunded | Cancelled |
| *anything else* | Quotation (safe default), to invoice |

When the website status later changes (e.g. from *paid* to *shipped*), the website
simply calls the script again **with the same `website_order_id`** and the new
status — the order updates in place.

---

## 7. Invoicing (optional)

The script can also create and post a customer invoice for a confirmed order. This
is **off by default** — your developers enable it by calling the
`create_and_post_invoice(...)` step. For business orders, the invoice is correctly
addressed to the **company**, not the individual.

---

## 8. Common questions & problems

**"Authentication failed."**
The login details are wrong or missing. Double‑check `ODOO_URL`, `ODOO_DB`,
`ODOO_USERNAME`, `ODOO_PASSWORD`. Prefer an **API key** over a password.

**"No product for SKU=… — line skipped."**
That product’s SKU/barcode does not exist in Odoo, so the line was left off the
order. Add the product in Odoo (or correct the SKU) so they match exactly.

**A customer appears twice in Odoo.**
The script matches people by **email**. If the website sends a different email (or
none), it can’t recognise the existing person. Make sure orders include a consistent
email.

**Can I run the same order twice?**
Yes — it’s safe. The script updates the existing order instead of duplicating it.

**Will this change confirmed/locked orders?**
Order *lines* are only safely replaced while the order is still a draft/quotation.
Once an order is confirmed and being fulfilled, line changes should be handled
manually — discuss this case with the Homekitfit team.

---

## 9. Cautions / good practice

- **Test on STAGING first.** Never point a new integration at the live system until
  it’s proven on staging.
- **Keep the login details secret.** Use environment variables, never paste them
  into the code or into chat/email.
- **Keep SKUs in sync.** The website and Odoo must use the same product SKUs/barcodes
  for product matching to work.
- **This file is an example to build on**, not the final production integration. Your
  developers will wire `create_or_update_order(...)` into the website so it runs
  automatically for every order.

---

*Questions about the Odoo side (credentials, products, company setup) → contact the
Homekitfit / Simonsky Solutions team.*
