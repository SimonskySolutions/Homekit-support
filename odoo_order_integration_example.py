#!/usr/bin/env python3


from __future__ import annotations

import os
import ssl
import xmlrpc.client
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# CONFIG
# --------------------------------------------------------------------------- #
ODOO_URL = os.environ.get("ODOO_URL", "https://your-company.odoo.com")
ODOO_DB = os.environ.get("ODOO_DB", "your-database-name")
ODOO_USERNAME = os.environ.get("ODOO_USERNAME", "integration@your-company.com")
ODOO_PASSWORD = os.environ.get("ODOO_PASSWORD", "your-api-key-or-password")

# If your Odoo is multi-company, set the company the orders belong to (or None).
ODOO_COMPANY_ID: Optional[int] = (
    int(os.environ["ODOO_COMPANY_ID"]) if os.environ.get("ODOO_COMPANY_ID") else None
)



STATUS_MAP: Dict[str, tuple] = {
    "pending": ("sent", "to invoice"),
    "authorized": ("sent", "to invoice"),
    "processing": ("sale", "to invoice"),
    "shipped": ("sale", "to invoice"),
    "paid": ("sale", "invoiced"),
    "completed": ("sale", "invoiced"),
    "fulfilled": ("sale", "invoiced"),
    "returned": ("sale", "invoiced"),
    "cancelled": ("cancel", "no"),
    "declined": ("cancel", "no"),
    "failed": ("cancel", "no"),
    "voided": ("cancel", "no"),
    "refunded": ("cancel", "no"),
}
# Anything not in the map falls back to a safe default:
DEFAULT_STATUS = ("sent", "to invoice")


# --------------------------------------------------------------------------- #
# thin wrapper around XML-RPC)
# --------------------------------------------------------------------------- #
class OdooClient:
    """Minimal Odoo External API client.

    Odoo exposes two XML-RPC endpoints:
        /xmlrpc/2/common  -> version info + authenticate (returns the user id)
        /xmlrpc/2/object  -> execute_kw(model, method, args, kwargs) for all ORM calls
    """

    def __init__(self, url: str, db: str, username: str, password: str):
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.password = password
        # allow_none lets us pass Python None as XML-RPC nil
        ctx = ssl.create_default_context()
        self.common = xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/common", context=ctx, allow_none=True
        )
        self.models = xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/object", context=ctx, allow_none=True
        )
        self.uid: Optional[int] = None

    def login(self) -> int:
        self.uid = self.common.authenticate(self.db, self.username, self.password, {})
        if not self.uid:
            raise RuntimeError("Odoo authentication failed: check DB/username/password.")
        return self.uid

    def execute(self, model: str, method: str, *args, **kwargs) -> Any:
        """Call any model method, e.g. execute('res.partner','search_read',[domain],{...})."""
        if not self.uid:
            self.login()
        return self.models.execute_kw(
            self.db, self.uid, self.password, model, method, list(args), kwargs
        )

    # Convenience helpers --------------------------------------------------- #
    def search(self, model: str, domain: List, limit: Optional[int] = None) -> List[int]:
        kw = {"limit": limit} if limit else {}
        return self.execute(model, "search", domain, **kw)

    def search_read(self, model: str, domain: List, fields: List[str],
                    limit: Optional[int] = None) -> List[Dict]:
        kw = {"fields": fields}
        if limit:
            kw["limit"] = limit
        return self.execute(model, "search_read", domain, **kw)

    def create(self, model: str, vals: Dict) -> int:
        return self.execute(model, "create", vals)

    def write(self, model: str, ids: List[int], vals: Dict) -> bool:
        return self.execute(model, "write", ids, vals)


# --------------------------------------------------------------------------- #
# CUSTOMER & COMPANY (res.partner)
# --------------------------------------------------------------------------- #
# In Odoo, both individuals and companies are res.partner records.
#   - A company has is_company = True.
#   - A person can be linked to a company via parent_id (shown as a child contact).
#   - The order's invoice address (partner_invoice_id) decides who the invoice is
#     billed to. For B2B we bill the COMPANY.
#
# Matching rules (to avoid duplicates):
#   - Person  -> matched by email.
#   - Company -> matched by VAT (Bulgarian EIK/Bulstat is stored in the VAT field).

def find_or_create_company(odoo: OdooClient, company: Optional[Dict]) -> Optional[int]:
    """company = {"name", "vat" (EIK/Bulstat or BG VAT), "street", "city", "zip", "country_code"}.

    Returns the company partner id, or None if no company info was supplied (B2C).
    """
    if not company:
        return None
    name = (company.get("name") or "").strip()
    vat = (company.get("vat") or "").strip()  # EIK/Bulstat goes here
    if not (name or vat):
        return None

    # 1) Try to match an existing company by VAT (most reliable, prevents dupes).
    if vat:
        found = odoo.search(
            "res.partner", [("is_company", "=", True), ("vat", "=", vat)], limit=1
        )
        if found:
            company_id = found[0]
            odoo.write("res.partner", [company_id], _company_vals(odoo, company))
            return company_id

    # 2) Otherwise create it.
    return odoo.create("res.partner", _company_vals(odoo, company))


def _company_vals(odoo: OdooClient, company: Dict) -> Dict:
    vals: Dict[str, Any] = {
        "is_company": True,
        "name": (company.get("name") or "Company").strip(),
    }
    if company.get("vat"):
        vals["vat"] = company["vat"].strip()
    _apply_address(odoo, vals, company)
    return vals


def find_or_create_person(odoo: OdooClient, customer: Dict,
                          company_id: Optional[int]) -> int:
    """customer = {"name", "email", "phone", "street", "city", "zip", "country_code"}.

    Matched by email. If a company_id is given and the person has no company yet,
    the person is linked under the company as a child contact.
    """
    email = (customer.get("email") or "").strip()
    person_id: Optional[int] = None
    if email:
        found = odoo.search("res.partner", [("email", "=", email)], limit=1)
        if found:
            person_id = found[0]

    vals: Dict[str, Any] = {
        "name": (customer.get("name") or "Website Customer").strip(),
        "email": email or None,
        "phone": customer.get("phone") or None,
    }
    _apply_address(odoo, vals, customer)

    if person_id:
        odoo.write("res.partner", [person_id], vals)
    else:
        person_id = odoo.create("res.partner", vals)

    # Link to the company without hijacking an existing relationship.
    if company_id:
        current = odoo.search_read(
            "res.partner", [("id", "=", person_id)], ["parent_id"], limit=1
        )
        if current and not current[0].get("parent_id"):
            odoo.write("res.partner", [person_id], {"parent_id": company_id})

    return person_id


def _apply_address(odoo: OdooClient, vals: Dict, src: Dict) -> None:
    for fld in ("street", "street2", "city", "zip"):
        if src.get(fld):
            vals[fld] = src[fld]
    code = (src.get("country_code") or "").strip().upper()
    if code:
        country = odoo.search("res.country", [("code", "=", code)], limit=1)
        if country:
            vals["country_id"] = country[0]


# --------------------------------------------------------------------------- #
# PRODUCT MATCHING (product.product)
# --------------------------------------------------------------------------- #
# Match each order line to an Odoo product by SKU (default_code) then barcode.
# Keep a small cache so repeated SKUs in one batch don't re-query.
_PRODUCT_CACHE: Dict[str, Optional[int]] = {}


def find_product(odoo: OdooClient, sku: str = "", barcode: str = "") -> Optional[int]:
    key = f"{sku}|{barcode}"
    if key in _PRODUCT_CACHE:
        return _PRODUCT_CACHE[key]
    product_id: Optional[int] = None
    if sku:
        found = odoo.search("product.product", [("default_code", "=", sku)], limit=1)
        if found:
            product_id = found[0]
    if not product_id and barcode:
        found = odoo.search("product.product", [("barcode", "=", barcode)], limit=1)
        if found:
            product_id = found[0]
    _PRODUCT_CACHE[key] = product_id
    return product_id


# --------------------------------------------------------------------------- #
# ORDER CREATION (sale.order) — IDEMPOTENT
# --------------------------------------------------------------------------- #
# IDEMPOTENCY is critical: the website may retry the same order (network errors,
# webhook re-delivery). We store the website's order number in client_order_ref
# and a unique tag in `origin`, then look it up before creating. Never create a
# second sale.order for the same source order.

def order_origin_tag(website_order_id: str) -> str:
    """Unique, searchable marker of the source order. Adapt the prefix to your site."""
    return f"WEB #{website_order_id}"


def find_existing_order(odoo: OdooClient, website_order_id: str) -> Optional[int]:
    found = odoo.search(
        "sale.order", [("origin", "=", order_origin_tag(website_order_id))], limit=1
    )
    return found[0] if found else None


def create_or_update_order(odoo: OdooClient, order: Dict) -> int:
    """order = {
        "website_order_id": "12345",          # your unique order id (required)
        "order_ref":        "WEB-12345",      # human ref shown to staff (optional)
        "status":           "paid",           # your status -> mapped below
        "currency_code":    "EUR",            # ISO code (optional)
        "customer":         {...},            # see find_or_create_person
        "company":          {...} or None,    # see find_or_create_company (B2B)
        "lines": [
            {"sku": "...", "barcode": "...", "name": "...",
             "qty": 1, "price_unit": 10.0, "discount_pct": 0.0},
            ...
        ],
    }
    Returns the Odoo sale.order id. Safe to call repeatedly (idempotent).
    """
    website_order_id = str(order["website_order_id"])

    # 1) Resolve customer & company partners.
    company_id = find_or_create_company(odoo, order.get("company"))
    person_id = find_or_create_person(odoo, order["customer"], company_id)
    invoice_partner_id = company_id or person_id  # bill the company for B2B

    # 2) Build order lines (Odoo "command" tuples: (0, 0, vals) = create new line).
    order_lines = []
    for line in order.get("lines", []):
        product_id = find_product(odoo, line.get("sku", ""), line.get("barcode", ""))
        if not product_id:
            # Decide your policy: skip, or map to a generic "Unknown product".
            print(f"  WARNING: no product for SKU={line.get('sku')} — line skipped")
            continue
        order_lines.append((0, 0, {
            "product_id": product_id,
            "name": line.get("name") or "",          # description shown on the order
            "product_uom_qty": float(line.get("qty", 1) or 1),
            "price_unit": float(line.get("price_unit", 0.0) or 0.0),
            "discount": float(line.get("discount_pct", 0.0) or 0.0),  # % discount
        }))

    # 3) Resolve currency (optional).
    currency_id = None
    if order.get("currency_code"):
        cur = odoo.search(
            "res.currency", [("name", "=", order["currency_code"].upper())], limit=1
        )
        currency_id = cur[0] if cur else None

    existing_id = find_existing_order(odoo, website_order_id)

    base_vals: Dict[str, Any] = {
        "partner_id": person_id,
        "partner_invoice_id": invoice_partner_id,
        "partner_shipping_id": person_id,
        "origin": order_origin_tag(website_order_id),
        "client_order_ref": order.get("order_ref") or website_order_id,
    }
    if currency_id:
        base_vals["currency_id"] = currency_id
    if ODOO_COMPANY_ID:
        base_vals["company_id"] = ODOO_COMPANY_ID

    if existing_id:
        # UPDATE path: refresh header + replace lines (only safe while draft/sent).
        odoo.write("sale.order", [existing_id], base_vals)
        _replace_order_lines(odoo, existing_id, order_lines)
        order_id = existing_id
        print(f"  Updated existing sale.order {order_id}")
    else:
        # CREATE path.
        create_vals = dict(base_vals)
        create_vals["order_line"] = order_lines
        order_id = odoo.create("sale.order", create_vals)
        print(f"  Created sale.order {order_id}")

    # 4) Apply status (confirm / cancel / invoice-ready).
    apply_order_status(odoo, order_id, order.get("status", ""))
    return order_id


def _replace_order_lines(odoo: OdooClient, order_id: int, new_lines: List) -> None:
    """Delete existing lines then add the new ones. Only do this while the order
    is still draft/sent (a confirmed/locked order should be handled differently)."""
    existing = odoo.search("sale.order.line", [("order_id", "=", order_id)])
    commands = [(2, lid, 0) for lid in existing]  # (2, id, 0) = delete line
    commands += new_lines
    odoo.write("sale.order", [order_id], {"order_line": commands})


# --------------------------------------------------------------------------- #
# STATUS UPDATES
# --------------------------------------------------------------------------- #
def apply_order_status(odoo: OdooClient, order_id: int, website_status: str) -> None:
    """Move the Odoo order to match the website status.

    sale.order state transitions are done via methods, not by writing `state`:
        action_confirm()  -> draft/sent  => sale
        action_cancel()   -> any         => cancel
        action_draft()    -> cancel      => draft (re-open)
    """
    state, _invoice_status = STATUS_MAP.get(
        (website_status or "").lower().strip(), DEFAULT_STATUS
    )
    current = odoo.search_read("sale.order", [("id", "=", order_id)], ["state"], limit=1)
    current_state = current[0]["state"] if current else "draft"

    if state == "sale" and current_state in ("draft", "sent"):
        odoo.execute("sale.order", "action_confirm", [order_id])
        print(f"  Confirmed sale.order {order_id}")
    elif state == "cancel" and current_state != "cancel":
        odoo.execute("sale.order", "action_cancel", [order_id])
        print(f"  Cancelled sale.order {order_id}")
    elif state == "sent" and current_state == "draft":
        odoo.write("sale.order", [order_id], {"state": "sent"})
        print(f"  Marked sale.order {order_id} as Quotation Sent")
    # If already in the desired state, nothing to do.


# --------------------------------------------------------------------------- #
# INVOICING (optional)
# --------------------------------------------------------------------------- #
def create_and_post_invoice(odoo: OdooClient, order_id: int) -> Optional[int]:
    """Create a customer invoice for a CONFIRMED order, then post it.

    The order must be in state 'sale' and have invoiceable lines. The invoice is
    billed to the order's partner_invoice_id (the company for B2B).
    """
    order = odoo.search_read(
        "sale.order", [("id", "=", order_id)], ["state", "invoice_status"], limit=1
    )
    if not order or order[0]["state"] != "sale":
        print(f"  Skip invoice: order {order_id} is not confirmed.")
        return None

    # _create_invoices is the supported server method to invoice a sale order.
    move_ids = odoo.execute("sale.order", "_create_invoices", [order_id])
    if not move_ids:
        print(f"  No invoice created for order {order_id}.")
        return None
    invoice_id = move_ids[0] if isinstance(move_ids, list) else move_ids
    odoo.execute("account.move", "action_post", [invoice_id])
    print(f"  Posted invoice {invoice_id} for order {order_id}")
    return invoice_id


# --------------------------------------------------------------------------- #
# DEMO
# --------------------------------------------------------------------------- #
def _demo() -> None:
    """End-to-end example. Replace the payload with real website data."""
    odoo = OdooClient(ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD)
    uid = odoo.login()
    print(f"Connected to Odoo as uid={uid}\n")

    example_order = {
        "website_order_id": "100245",
        "order_ref": "WEB-100245",
        "status": "paid",
        "currency_code": "EUR",
        "customer": {
            "name": "Vladimir Velinov",
            "email": "admin@vilusion.com",
            "phone": "+359876566572",
            "street": "ул. Житница 21",
            "city": "София",
            "zip": "1612",
            "country_code": "BG",
        },
        # Set company = None for a normal B2C order.
        "company": {
            "name": "Вилюжън ЕООД",
            "vat": "207266621",          # Bulgarian EIK/Bulstat -> Odoo VAT field
            "street": "ул. Житница 21",
            "city": "София",
            "zip": "1612",
            "country_code": "BG",
        },
        "lines": [
            {"sku": "SHELLY-PLUS-1", "name": "Shelly Plus 1",
             "qty": 2, "price_unit": 18.50, "discount_pct": 0.0},
            {"sku": "HUE-BLOOM", "name": "Philips Hue Bloom",
             "qty": 1, "price_unit": 79.90, "discount_pct": 10.0},
        ],
    }

    order_id = create_or_update_order(odoo, example_order)

    # Later, when the website status changes, call again with the same
    # website_order_id and the new status — it updates in place:
    #   example_order["status"] = "shipped"
    #   create_or_update_order(odoo, example_order)

    # Optionally invoice once paid/confirmed:
    # create_and_post_invoice(odoo, order_id)

    print(f"\nDone. Odoo sale.order id = {order_id}")


if __name__ == "__main__":
    _demo()
