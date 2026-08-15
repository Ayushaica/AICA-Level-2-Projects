#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
 SMART BILLING MANAGER
 Professional Offline Desktop Billing Software (GST-Compliant, India)
----------------------------------------------------------------------------
 Architecture (strict layer separation within a single file):

   1. UI LAYER        -> PyQt5 widgets (MainWindow, dialogs). The UI NEVER
                         touches the database directly; it only calls the
                         Logic layer.
   2. LOGIC LAYER     -> BillingLogic: calculations, validation, invoice
                         numbering, backup/restore, PDF/print workflow.
   3. DATABASE LAYER  -> DatabaseManager: SQLite storage, schema creation,
                         CRUD operations, parameterised queries.

 Folders auto-created beside the .py / .exe:
   /database        -> billing.db (SQLite)
   /invoices_pdf    -> exported PDF invoices
   /backup          -> timestamped database backups

 Dependencies:  pip install PyQt5 reportlab
 EXE build:     pyinstaller --onefile --noconsole smart_billing_manager.py
                (Paths resolve via sys.executable when frozen, so the
                 database lives next to the EXE / installed program files.)
 Author: CA Akhil Pachori  |  Contact: +91-7737109999
============================================================================
"""

import os
import re
import sys
import shutil
import sqlite3
import traceback
from datetime import datetime

from PyQt5.QtCore import Qt, QDate
from PyQt5.QtGui import QFont, QColor, QIcon
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QFileDialog, QDialog, QTextBrowser, QDateEdit,
    QGroupBox, QAbstractItemView, QDoubleSpinBox, QComboBox, QSplitter,
    QDialogButtonBox, QFormLayout, QTextEdit
)
from PyQt5.QtPrintSupport import QPrinter, QPrintDialog

# ReportLab (PDF)
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
)

APP_NAME = "Smart Billing Manager"
APP_VERSION = "1.0.0"


# ============================================================================
#  PATH / FOLDER MANAGEMENT  (works for .py, PyInstaller EXE, Inno Setup)
# ============================================================================
def app_base_dir() -> str:
    """Directory containing the running .py file OR the frozen .exe."""
    if getattr(sys, "frozen", False):           # PyInstaller EXE
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = app_base_dir()
DB_DIR = os.path.join(BASE_DIR, "database")
PDF_DIR = os.path.join(BASE_DIR, "invoices_pdf")
BACKUP_DIR = os.path.join(BASE_DIR, "backup")
DB_PATH = os.path.join(DB_DIR, "billing.db")


def ensure_folders():
    for folder in (DB_DIR, PDF_DIR, BACKUP_DIR):
        os.makedirs(folder, exist_ok=True)


# ============================================================================
#  DATABASE LAYER  (SQLite only — no business logic, no UI)
# ============================================================================
class DatabaseManager:
    """All SQL lives here. Every method is a small, reusable operation."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = None
        self.connect()
        self.create_schema()

    # ---- connection management -------------------------------------------
    def connect(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")

    def close(self):
        if self.conn:
            try:
                self.conn.commit()
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def reconnect(self):
        self.close()
        self.connect()

    # ---- schema ------------------------------------------------------------
    def create_schema(self):
        cur = self.conn.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS seller (
            id            INTEGER PRIMARY KEY CHECK (id = 1),
            trade_name    TEXT NOT NULL DEFAULT '',
            address       TEXT NOT NULL DEFAULT '',
            gstin         TEXT NOT NULL DEFAULT '',
            contact       TEXT NOT NULL DEFAULT '',
            bank_details  TEXT NOT NULL DEFAULT '',
            declaration   TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS invoices (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no     TEXT NOT NULL UNIQUE,
            invoice_date   TEXT NOT NULL,
            cust_name      TEXT NOT NULL,
            cust_address   TEXT DEFAULT '',
            cust_mobile    TEXT DEFAULT '',
            cust_gstin     TEXT DEFAULT '',
            subtotal       REAL NOT NULL DEFAULT 0,
            total_gst      REAL NOT NULL DEFAULT 0,
            round_off      REAL NOT NULL DEFAULT 0,
            grand_total    REAL NOT NULL DEFAULT 0,
            created_at     TEXT DEFAULT (datetime('now','localtime')),
            updated_at     TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS invoice_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id  INTEGER NOT NULL REFERENCES invoices(id)
                        ON DELETE CASCADE,
            item_name   TEXT NOT NULL,
            qty         REAL NOT NULL,
            rate        REAL NOT NULL,
            gst_pct     REAL NOT NULL,
            amount      REAL NOT NULL,
            gst_amount  REAL NOT NULL,
            total       REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tax_summary (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id  INTEGER NOT NULL REFERENCES invoices(id)
                        ON DELETE CASCADE,
            gst_pct     REAL NOT NULL,
            taxable     REAL NOT NULL,
            gst_amount  REAL NOT NULL
        );
        """)
        # seed seller row
        cur.execute("SELECT COUNT(*) FROM seller")
        if cur.fetchone()[0] == 0:
            cur.execute("""INSERT INTO seller
                (id, trade_name, address, gstin, contact, bank_details, declaration)
                VALUES (1, 'My Trade Name', 'My Address, City, State - PIN',
                        '', 'Mobile: ______  Email: ______',
                        'Bank: ______  A/c No: ______  IFSC: ______',
                        'We declare that this invoice shows the actual price of the goods described and that all particulars are true and correct.')""")
        self.conn.commit()

    # ---- seller ------------------------------------------------------------
    def get_seller(self) -> dict:
        row = self.conn.execute("SELECT * FROM seller WHERE id = 1").fetchone()
        return dict(row) if row else {}

    def save_seller(self, data: dict):
        self.conn.execute("""UPDATE seller SET trade_name=?, address=?, gstin=?,
                             contact=?, bank_details=?, declaration=? WHERE id=1""",
                          (data["trade_name"], data["address"], data["gstin"],
                           data["contact"], data["bank_details"], data["declaration"]))
        self.conn.commit()

    # ---- invoice numbering --------------------------------------------------
    def last_invoice_serial(self, prefix: str) -> int:
        rows = self.conn.execute(
            "SELECT invoice_no FROM invoices WHERE invoice_no LIKE ?",
            (prefix + "%",)).fetchall()
        max_serial = 0
        for r in rows:
            m = re.search(r"(\d+)$", r["invoice_no"])
            if m:
                max_serial = max(max_serial, int(m.group(1)))
        return max_serial

    def invoice_no_exists(self, invoice_no: str, exclude_id=None) -> bool:
        if exclude_id:
            row = self.conn.execute(
                "SELECT 1 FROM invoices WHERE invoice_no=? AND id<>?",
                (invoice_no, exclude_id)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT 1 FROM invoices WHERE invoice_no=?",
                (invoice_no,)).fetchone()
        return row is not None

    # ---- invoice CRUD --------------------------------------------------------
    def insert_invoice(self, inv: dict, items: list, tax_rows: list) -> int:
        cur = self.conn.cursor()
        cur.execute("""INSERT INTO invoices
            (invoice_no, invoice_date, cust_name, cust_address, cust_mobile,
             cust_gstin, subtotal, total_gst, round_off, grand_total)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (inv["invoice_no"], inv["invoice_date"], inv["cust_name"],
             inv["cust_address"], inv["cust_mobile"], inv["cust_gstin"],
             inv["subtotal"], inv["total_gst"], inv["round_off"],
             inv["grand_total"]))
        invoice_id = cur.lastrowid
        self._write_children(invoice_id, items, tax_rows)
        self.conn.commit()
        return invoice_id

    def update_invoice(self, invoice_id: int, inv: dict, items: list,
                       tax_rows: list):
        self.conn.execute("""UPDATE invoices SET invoice_no=?, invoice_date=?,
            cust_name=?, cust_address=?, cust_mobile=?, cust_gstin=?,
            subtotal=?, total_gst=?, round_off=?, grand_total=?,
            updated_at=datetime('now','localtime') WHERE id=?""",
            (inv["invoice_no"], inv["invoice_date"], inv["cust_name"],
             inv["cust_address"], inv["cust_mobile"], inv["cust_gstin"],
             inv["subtotal"], inv["total_gst"], inv["round_off"],
             inv["grand_total"], invoice_id))
        self.conn.execute("DELETE FROM invoice_items WHERE invoice_id=?",
                          (invoice_id,))
        self.conn.execute("DELETE FROM tax_summary WHERE invoice_id=?",
                          (invoice_id,))
        self._write_children(invoice_id, items, tax_rows)
        self.conn.commit()

    def _write_children(self, invoice_id: int, items: list, tax_rows: list):
        self.conn.executemany("""INSERT INTO invoice_items
            (invoice_id, item_name, qty, rate, gst_pct, amount, gst_amount, total)
            VALUES (?,?,?,?,?,?,?,?)""",
            [(invoice_id, i["item_name"], i["qty"], i["rate"], i["gst_pct"],
              i["amount"], i["gst_amount"], i["total"]) for i in items])
        self.conn.executemany("""INSERT INTO tax_summary
            (invoice_id, gst_pct, taxable, gst_amount) VALUES (?,?,?,?)""",
            [(invoice_id, t["gst_pct"], t["taxable"], t["gst_amount"])
             for t in tax_rows])

    def get_invoice(self, invoice_id: int):
        inv = self.conn.execute("SELECT * FROM invoices WHERE id=?",
                                (invoice_id,)).fetchone()
        if not inv:
            return None, [], []
        items = self.conn.execute(
            "SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY id",
            (invoice_id,)).fetchall()
        taxes = self.conn.execute(
            "SELECT * FROM tax_summary WHERE invoice_id=? ORDER BY gst_pct",
            (invoice_id,)).fetchall()
        return dict(inv), [dict(i) for i in items], [dict(t) for t in taxes]

    def delete_invoice(self, invoice_id: int):
        self.conn.execute("DELETE FROM invoices WHERE id=?", (invoice_id,))
        self.conn.commit()

    def search_invoices(self, term: str = "") -> list:
        like = f"%{term.strip()}%"
        rows = self.conn.execute("""SELECT id, invoice_no, invoice_date,
            cust_name, cust_mobile, grand_total FROM invoices
            WHERE invoice_no LIKE ? OR cust_name LIKE ? OR cust_mobile LIKE ?
            ORDER BY id DESC LIMIT 500""", (like, like, like)).fetchall()
        return [dict(r) for r in rows]


# ============================================================================
#  LOGIC LAYER  (calculations, validation, numbering, backup, PDF)
# ============================================================================
class BillingLogic:
    """Business rules. Talks to DatabaseManager; never imports UI classes."""

    INVOICE_PREFIX = "INV-"

    def __init__(self, db: DatabaseManager):
        self.db = db

    # ---- calculations -------------------------------------------------------
    @staticmethod
    def calc_line(qty: float, rate: float, gst_pct: float) -> dict:
        amount = round(qty * rate, 2)
        gst_amount = round(amount * gst_pct / 100.0, 2)
        return {"amount": amount, "gst_amount": gst_amount,
                "total": round(amount + gst_amount, 2)}

    @staticmethod
    def calc_totals(items: list) -> dict:
        subtotal = round(sum(i["amount"] for i in items), 2)
        total_gst = round(sum(i["gst_amount"] for i in items), 2)
        raw_total = subtotal + total_gst
        grand_total = float(round(raw_total))          # round to nearest rupee
        round_off = round(grand_total - raw_total, 2)
        return {"subtotal": subtotal, "total_gst": total_gst,
                "round_off": round_off, "grand_total": grand_total}

    @staticmethod
    def tax_summary(items: list) -> list:
        buckets = {}
        for i in items:
            b = buckets.setdefault(i["gst_pct"], {"taxable": 0.0, "gst": 0.0})
            b["taxable"] += i["amount"]
            b["gst"] += i["gst_amount"]
        return [{"gst_pct": pct,
                 "taxable": round(v["taxable"], 2),
                 "gst_amount": round(v["gst"], 2)}
                for pct, v in sorted(buckets.items())]

    # ---- validation -----------------------------------------------------------
    @staticmethod
    def validate_invoice(inv: dict, items: list) -> list:
        errors = []
        if not inv["invoice_no"].strip():
            errors.append("Invoice number is required.")
        if not inv["cust_name"].strip():
            errors.append("Customer name is required.")
        if inv["cust_mobile"] and not re.fullmatch(r"[0-9+\-\s]{6,15}",
                                                   inv["cust_mobile"]):
            errors.append("Mobile number appears invalid.")
        if inv["cust_gstin"] and len(inv["cust_gstin"].strip()) not in (0, 15):
            errors.append("Customer GSTIN must be 15 characters (or blank).")
        if not items:
            errors.append("At least one item is required.")
        for n, i in enumerate(items, start=1):
            if not i["item_name"].strip():
                errors.append(f"Row {n}: item name is blank.")
            if i["qty"] <= 0:
                errors.append(f"Row {n}: quantity must be greater than zero.")
            if i["rate"] < 0:
                errors.append(f"Row {n}: rate cannot be negative.")
        return errors

    # ---- invoice numbering -----------------------------------------------------
    def next_invoice_no(self) -> str:
        serial = self.db.last_invoice_serial(self.INVOICE_PREFIX) + 1
        return f"{self.INVOICE_PREFIX}{serial:05d}"

    # ---- save / update / delete / search ----------------------------------------
    def save_invoice(self, invoice_id, inv: dict, items: list):
        errors = self.validate_invoice(inv, items)
        if errors:
            return None, errors
        if self.db.invoice_no_exists(inv["invoice_no"], exclude_id=invoice_id):
            return None, [f"Invoice number {inv['invoice_no']} already exists."]
        inv.update(self.calc_totals(items))
        taxes = self.tax_summary(items)
        if invoice_id:
            self.db.update_invoice(invoice_id, inv, items, taxes)
            return invoice_id, []
        return self.db.insert_invoice(inv, items, taxes), []

    def load_invoice(self, invoice_id: int):
        return self.db.get_invoice(invoice_id)

    def delete_invoice(self, invoice_id: int):
        self.db.delete_invoice(invoice_id)

    def search(self, term: str):
        return self.db.search_invoices(term)

    # ---- backup / restore ---------------------------------------------------------
    def backup_database(self, target_path: str = None) -> str:
        if target_path is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target_path = os.path.join(BACKUP_DIR,
                                       f"billing_backup_{stamp}.db")
            n = 1
            while os.path.exists(target_path):     # avoid same-second clash
                target_path = os.path.join(
                    BACKUP_DIR, f"billing_backup_{stamp}_{n}.db")
                n += 1
        self.db.conn.commit()
        dest = sqlite3.connect(target_path)
        try:
            self.db.conn.backup(dest)      # safe online backup
        finally:
            dest.close()
        return target_path

    @staticmethod
    def is_valid_sqlite(path: str) -> bool:
        try:
            if os.path.getsize(path) < 100:
                return False
            with open(path, "rb") as f:
                if f.read(16) != b"SQLite format 3\x00":
                    return False
            probe = sqlite3.connect(path)
            try:
                names = {r[0] for r in probe.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                probe.execute("PRAGMA integrity_check").fetchone()
            finally:
                probe.close()
            return {"invoices", "invoice_items"}.issubset(names)
        except Exception:
            return False

    def restore_database(self, source_path: str) -> str:
        if not self.is_valid_sqlite(source_path):
            raise ValueError("Selected file is not a valid Smart Billing "
                             "Manager database backup.")
        safety = self.backup_database()            # auto-backup current data
        self.db.close()                            # release lock before copy
        try:
            shutil.copy2(source_path, self.db.db_path)
        finally:
            self.db.connect()
            self.db.create_schema()
        return safety

    # ---- PDF (ReportLab) --------------------------------------------------------
    def export_pdf(self, inv: dict, items: list, taxes: list,
                   seller: dict, path: str = None) -> str:
        if path is None:
            safe_no = re.sub(r"[^A-Za-z0-9_-]", "_", inv["invoice_no"])
            path = os.path.join(PDF_DIR, f"Invoice_{safe_no}.pdf")

        doc = SimpleDocTemplate(path, pagesize=A4,
                                leftMargin=14 * mm, rightMargin=14 * mm,
                                topMargin=12 * mm, bottomMargin=12 * mm)
        blue = colors.HexColor("#1a4f9c")
        red = colors.HexColor("#c0392b")
        s_title = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=16,
                                 textColor=blue, alignment=1)
        s_head = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=10,
                                textColor=blue)
        s_norm = ParagraphStyle("n", fontName="Helvetica", fontSize=9,
                                leading=12)
        s_small = ParagraphStyle("s", fontName="Helvetica", fontSize=8,
                                 textColor=colors.grey, leading=10)
        story = []

        # Seller header
        story.append(Paragraph(seller.get("trade_name", ""), s_title))
        story.append(Paragraph(seller.get("address", ""),
                     ParagraphStyle("a", parent=s_norm, alignment=1)))
        hdr_line = " | ".join(x for x in
                              [f"GSTIN: {seller.get('gstin','')}" if seller.get("gstin") else "",
                               seller.get("contact", "")] if x)
        if hdr_line:
            story.append(Paragraph(hdr_line,
                         ParagraphStyle("c", parent=s_norm, alignment=1)))
        story.append(Spacer(1, 3 * mm))
        story.append(HRFlowable(width="100%", thickness=1.2, color=red))
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph("TAX INVOICE",
                     ParagraphStyle("ti", parent=s_head, alignment=1,
                                    textColor=red, fontSize=11)))
        story.append(Spacer(1, 3 * mm))

        # Invoice meta + customer
        meta = Table([
            [Paragraph("<b>Bill To:</b>", s_norm),
             Paragraph(f"<b>Invoice No:</b> {inv['invoice_no']}", s_norm)],
            [Paragraph(inv["cust_name"], s_norm),
             Paragraph(f"<b>Date:</b> {inv['invoice_date']}", s_norm)],
            [Paragraph(inv.get("cust_address", "") or "-", s_norm),
             Paragraph(f"<b>Mobile:</b> {inv.get('cust_mobile','') or '-'}", s_norm)],
            [Paragraph(f"GSTIN: {inv.get('cust_gstin','') or '-'}", s_norm), ""],
        ], colWidths=[110 * mm, 70 * mm])
        meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(meta)
        story.append(Spacer(1, 4 * mm))

        # Item table
        head = ["#", "Item Description", "Qty", "Rate", "Amount",
                "GST %", "GST Amt", "Total"]
        data = [head]
        for n, i in enumerate(items, start=1):
            data.append([str(n), Paragraph(i["item_name"], s_norm),
                         f"{i['qty']:g}", f"{i['rate']:.2f}",
                         f"{i['amount']:.2f}", f"{i['gst_pct']:g}%",
                         f"{i['gst_amount']:.2f}", f"{i['total']:.2f}"])
        tbl = Table(data, colWidths=[9 * mm, 63 * mm, 14 * mm, 20 * mm,
                                     23 * mm, 14 * mm, 20 * mm, 23 * mm],
                    repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), blue),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9db4d4")),
            ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#f2f6fc")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 4 * mm))

        # Tax summary + totals side by side
        tax_data = [["GST %", "Taxable", "CGST", "SGST", "Total GST"]]
        for t in taxes:
            half = round(t["gst_amount"] / 2, 2)
            tax_data.append([f"{t['gst_pct']:g}%", f"{t['taxable']:.2f}",
                             f"{half:.2f}", f"{half:.2f}",
                             f"{t['gst_amount']:.2f}"])
        tax_tbl = Table(tax_data, colWidths=[16 * mm, 22 * mm, 20 * mm,
                                             20 * mm, 22 * mm])
        tax_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef8")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9db4d4")),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ]))
        tot_data = [
            ["Subtotal", f"{inv['subtotal']:.2f}"],
            ["Total GST", f"{inv['total_gst']:.2f}"],
            ["Round Off", f"{inv['round_off']:+.2f}"],
            ["GRAND TOTAL (Rs.)", f"{inv['grand_total']:.2f}"],
        ]
        tot_tbl = Table(tot_data, colWidths=[42 * mm, 30 * mm])
        tot_tbl.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LINEABOVE", (0, -1), (-1, -1), 0.8, red),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("TEXTCOLOR", (0, -1), (-1, -1), red),
        ]))
        combo = Table([[tax_tbl, tot_tbl]], colWidths=[104 * mm, 76 * mm])
        combo.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(combo)
        story.append(Spacer(1, 6 * mm))

        # Footer: bank details + declaration + signature
        story.append(HRFlowable(width="100%", thickness=0.6, color=blue))
        story.append(Spacer(1, 2 * mm))
        if seller.get("bank_details"):
            story.append(Paragraph("<b>Bank Details:</b> " +
                                   seller["bank_details"], s_norm))
        if seller.get("declaration"):
            story.append(Spacer(1, 1.5 * mm))
            story.append(Paragraph("<b>Declaration:</b> " +
                                   seller["declaration"], s_small))
        story.append(Spacer(1, 12 * mm))
        sig = Table([[Paragraph("Customer Signature", s_norm),
                      Paragraph(f"For {seller.get('trade_name','')}"
                                "<br/><br/><br/>Authorised Signatory",
                                ParagraphStyle("sig", parent=s_norm,
                                               alignment=2))]],
                    colWidths=[90 * mm, 90 * mm])
        story.append(sig)
        doc.build(story)
        return path

    # ---- HTML render (shared by preview + print so print matches PDF) ----------
    @staticmethod
    def render_html(inv: dict, items: list, taxes: list, seller: dict) -> str:
        rows = ""
        for n, i in enumerate(items, start=1):
            rows += (f"<tr><td align='center'>{n}</td><td>{i['item_name']}</td>"
                     f"<td align='right'>{i['qty']:g}</td>"
                     f"<td align='right'>{i['rate']:.2f}</td>"
                     f"<td align='right'>{i['amount']:.2f}</td>"
                     f"<td align='right'>{i['gst_pct']:g}%</td>"
                     f"<td align='right'>{i['gst_amount']:.2f}</td>"
                     f"<td align='right'>{i['total']:.2f}</td></tr>")
        tax_rows = ""
        for t in taxes:
            half = round(t["gst_amount"] / 2, 2)
            tax_rows += (f"<tr><td>{t['gst_pct']:g}%</td>"
                         f"<td align='right'>{t['taxable']:.2f}</td>"
                         f"<td align='right'>{half:.2f}</td>"
                         f"<td align='right'>{half:.2f}</td>"
                         f"<td align='right'>{t['gst_amount']:.2f}</td></tr>")
        return f"""
        <html><body style="font-family:Arial; font-size:10pt; color:#222;">
        <div align="center">
          <span style="font-size:16pt; font-weight:bold; color:#1a4f9c;">
            {seller.get('trade_name','')}</span><br>
          {seller.get('address','')}<br>
          {('GSTIN: ' + seller['gstin'] + ' | ') if seller.get('gstin') else ''}
          {seller.get('contact','')}
        </div>
        <hr style="border:1px solid #c0392b;">
        <div align="center" style="color:#c0392b; font-weight:bold;">TAX INVOICE</div>
        <table width="100%" cellpadding="2">
          <tr><td><b>Bill To:</b> {inv['cust_name']}</td>
              <td align="right"><b>Invoice No:</b> {inv['invoice_no']}</td></tr>
          <tr><td>{inv.get('cust_address','') or ''}</td>
              <td align="right"><b>Date:</b> {inv['invoice_date']}</td></tr>
          <tr><td>Mobile: {inv.get('cust_mobile','') or '-'} &nbsp;
                  GSTIN: {inv.get('cust_gstin','') or '-'}</td><td></td></tr>
        </table>
        <table width="100%" border="1" cellspacing="0" cellpadding="3"
               style="border-collapse:collapse;">
          <tr bgcolor="#1a4f9c" style="color:white;">
            <th>#</th><th>Item Description</th><th>Qty</th><th>Rate</th>
            <th>Amount</th><th>GST %</th><th>GST Amt</th><th>Total</th></tr>
          {rows}
        </table><br>
        <table width="100%"><tr><td width="55%" valign="top">
          <table border="1" cellspacing="0" cellpadding="3"
                 style="border-collapse:collapse;">
            <tr bgcolor="#e8eef8"><th>GST %</th><th>Taxable</th>
                <th>CGST</th><th>SGST</th><th>Total GST</th></tr>
            {tax_rows}
          </table></td>
          <td valign="top" align="right">
          <table cellpadding="2">
            <tr><td>Subtotal:</td><td align="right">{inv['subtotal']:.2f}</td></tr>
            <tr><td>Total GST:</td><td align="right">{inv['total_gst']:.2f}</td></tr>
            <tr><td>Round Off:</td><td align="right">{inv['round_off']:+.2f}</td></tr>
            <tr style="color:#c0392b; font-weight:bold;">
              <td>GRAND TOTAL (Rs.):</td>
              <td align="right">{inv['grand_total']:.2f}</td></tr>
          </table></td></tr></table>
        <hr style="border:0.5px solid #1a4f9c;">
        <b>Bank Details:</b> {seller.get('bank_details','')}<br>
        <span style="font-size:8pt; color:#555;">
          <b>Declaration:</b> {seller.get('declaration','')}</span>
        <br><br><br>
        <table width="100%"><tr>
          <td>Customer Signature</td>
          <td align="right">For {seller.get('trade_name','')}<br><br><br>
              Authorised Signatory</td></tr></table>
        </body></html>"""


# ============================================================================
#  UI LAYER  (PyQt5) — talks ONLY to BillingLogic
# ============================================================================
STYLE_SHEET = """
QMainWindow, QDialog { background: #ffffff; }
QGroupBox {
    border: 1px solid #1a4f9c; border-radius: 6px;
    margin-top: 10px; font-weight: bold; color: #1a4f9c;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QLabel { color: #222; }
QLineEdit, QDateEdit, QDoubleSpinBox, QComboBox, QTextEdit {
    border: 1px solid #9db4d4; border-radius: 4px; padding: 4px;
    background: #ffffff;
}
QLineEdit:focus, QDateEdit:focus { border: 1.5px solid #1a4f9c; }
QPushButton {
    background: #1a4f9c; color: white; border: none; border-radius: 5px;
    padding: 7px 14px; font-weight: bold;
}
QPushButton:hover { background: #123d7d; }
QPushButton#danger { background: #c0392b; }
QPushButton#danger:hover { background: #96281b; }
QPushButton#neutral { background: #5d6d7e; }
QTableWidget {
    gridline-color: #c7d5ea; selection-background-color: #d6e4f7;
    selection-color: #000;
}
QHeaderView::section {
    background: #1a4f9c; color: white; font-weight: bold;
    padding: 5px; border: none;
}
"""


class SellerDialog(QDialog):
    """Edit seller (header) details."""

    def __init__(self, seller: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Seller / Business Details")
        self.setMinimumWidth(480)
        form = QFormLayout(self)
        self.trade = QLineEdit(seller.get("trade_name", ""))
        self.addr = QLineEdit(seller.get("address", ""))
        self.gstin = QLineEdit(seller.get("gstin", ""))
        self.contact = QLineEdit(seller.get("contact", ""))
        self.bank = QTextEdit(seller.get("bank_details", ""))
        self.bank.setFixedHeight(56)
        self.decl = QTextEdit(seller.get("declaration", ""))
        self.decl.setFixedHeight(56)
        form.addRow("Trade Name*", self.trade)
        form.addRow("Address", self.addr)
        form.addRow("GST Number", self.gstin)
        form.addRow("Contact", self.contact)
        form.addRow("Bank Details", self.bank)
        form.addRow("Declaration", self.decl)
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def data(self) -> dict:
        return {"trade_name": self.trade.text().strip(),
                "address": self.addr.text().strip(),
                "gstin": self.gstin.text().strip().upper(),
                "contact": self.contact.text().strip(),
                "bank_details": self.bank.toPlainText().strip(),
                "declaration": self.decl.toPlainText().strip()}


class SearchDialog(QDialog):
    """Search & pick a saved invoice."""

    def __init__(self, logic: BillingLogic, parent=None):
        super().__init__(parent)
        self.logic = logic
        self.selected_id = None
        self.setWindowTitle("Search Invoices")
        self.resize(680, 420)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.term = QLineEdit()
        self.term.setPlaceholderText("Invoice no. / customer name / mobile "
                                     "(leave blank for all)")
        btn = QPushButton("Search")
        btn.clicked.connect(self.run)
        self.term.returnPressed.connect(self.run)
        row.addWidget(self.term)
        row.addWidget(btn)
        lay.addLayout(row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Invoice No", "Date", "Customer", "Mobile", "Grand Total"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.doubleClicked.connect(self.pick)
        lay.addWidget(self.table)
        row2 = QHBoxLayout()
        open_btn = QPushButton("Open Selected")
        open_btn.clicked.connect(self.pick)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("neutral")
        close_btn.clicked.connect(self.reject)
        row2.addStretch()
        row2.addWidget(open_btn)
        row2.addWidget(close_btn)
        lay.addLayout(row2)
        self.run()

    def run(self):
        self.rows = self.logic.search(self.term.text())
        self.table.setRowCount(0)
        for r in self.rows:
            n = self.table.rowCount()
            self.table.insertRow(n)
            for c, key in enumerate(("invoice_no", "invoice_date",
                                     "cust_name", "cust_mobile")):
                self.table.setItem(n, c, QTableWidgetItem(str(r[key] or "")))
            amt = QTableWidgetItem(f"{r['grand_total']:.2f}")
            amt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(n, 4, amt)

    def pick(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, APP_NAME, "Select an invoice first.")
            return
        self.selected_id = self.rows[row]["id"]
        self.accept()


class MainWindow(QMainWindow):
    COL_ITEM, COL_QTY, COL_RATE, COL_GST, COL_AMT, COL_GSTAMT, COL_TOT = range(7)

    def __init__(self, logic: BillingLogic):
        super().__init__()
        self.logic = logic
        self.current_invoice_id = None
        self._loading = False
        self.setWindowTitle(f"{APP_NAME}  v{APP_VERSION}")
        self.resize(1220, 760)
        self._build_ui()
        self.new_invoice()

    # ---------------- UI construction ----------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Seller header banner
        seller = self.logic.db.get_seller()
        head = QHBoxLayout()
        self.seller_lbl = QLabel()
        self.seller_lbl.setStyleSheet(
            "color:#1a4f9c; font-size:15pt; font-weight:bold;")
        self._refresh_seller_banner(seller)
        edit_seller = QPushButton("Edit Seller Details")
        edit_seller.setObjectName("neutral")
        edit_seller.clicked.connect(self.edit_seller)
        head.addWidget(self.seller_lbl)
        head.addStretch()
        head.addWidget(edit_seller)
        root.addLayout(head)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        # ---- left: entry panel
        left = QWidget()
        ll = QVBoxLayout(left)

        inv_grp = QGroupBox("Invoice Details")
        g = QGridLayout(inv_grp)
        self.inv_no = QLineEdit()
        self.inv_no.setReadOnly(True)
        self.inv_no.setStyleSheet("background:#eef3fb; font-weight:bold;")
        self.inv_date = QDateEdit(QDate.currentDate())
        self.inv_date.setCalendarPopup(True)
        self.inv_date.setDisplayFormat("dd-MM-yyyy")
        g.addWidget(QLabel("Invoice No."), 0, 0)
        g.addWidget(self.inv_no, 0, 1)
        g.addWidget(QLabel("Invoice Date"), 0, 2)
        g.addWidget(self.inv_date, 0, 3)
        ll.addWidget(inv_grp)

        cust_grp = QGroupBox("Customer Details")
        g2 = QGridLayout(cust_grp)
        self.c_name = QLineEdit(); self.c_name.setPlaceholderText("Customer name *")
        self.c_addr = QLineEdit(); self.c_addr.setPlaceholderText("Address")
        self.c_mob = QLineEdit(); self.c_mob.setPlaceholderText("Mobile")
        self.c_gst = QLineEdit(); self.c_gst.setPlaceholderText("GSTIN (optional)")
        g2.addWidget(QLabel("Name*"), 0, 0); g2.addWidget(self.c_name, 0, 1)
        g2.addWidget(QLabel("Mobile"), 0, 2); g2.addWidget(self.c_mob, 0, 3)
        g2.addWidget(QLabel("Address"), 1, 0); g2.addWidget(self.c_addr, 1, 1)
        g2.addWidget(QLabel("GSTIN"), 1, 2); g2.addWidget(self.c_gst, 1, 3)
        ll.addWidget(cust_grp)

        item_grp = QGroupBox("Items")
        iv = QVBoxLayout(item_grp)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Item Name", "Qty", "Rate", "GST %", "Amount", "GST Amt", "Total"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, 7):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self.on_item_changed)
        iv.addWidget(self.table)
        brow = QHBoxLayout()
        add_btn = QPushButton("+ Add Row")
        add_btn.clicked.connect(lambda: self.add_row())
        del_btn = QPushButton("Delete Row")
        del_btn.setObjectName("danger")
        del_btn.clicked.connect(self.delete_row)
        brow.addWidget(add_btn); brow.addWidget(del_btn); brow.addStretch()
        iv.addLayout(brow)
        ll.addWidget(item_grp, 1)

        tot_grp = QGroupBox("Totals")
        tg = QGridLayout(tot_grp)
        self.l_sub = QLabel("0.00"); self.l_gst = QLabel("0.00")
        self.l_ro = QLabel("0.00"); self.l_grand = QLabel("0.00")
        self.l_grand.setStyleSheet(
            "color:#c0392b; font-size:14pt; font-weight:bold;")
        for lbl in (self.l_sub, self.l_gst, self.l_ro):
            lbl.setStyleSheet("font-weight:bold;")
        tg.addWidget(QLabel("Subtotal:"), 0, 0); tg.addWidget(self.l_sub, 0, 1)
        tg.addWidget(QLabel("Total GST:"), 0, 2); tg.addWidget(self.l_gst, 0, 3)
        tg.addWidget(QLabel("Round Off:"), 0, 4); tg.addWidget(self.l_ro, 0, 5)
        tg.addWidget(QLabel("Grand Total:"), 0, 6)
        tg.addWidget(self.l_grand, 0, 7)
        ll.addWidget(tot_grp)

        # action buttons
        act = QGridLayout()
        buttons = [
            ("New", self.new_invoice, ""), ("Save", self.save_invoice, ""),
            ("Print", self.print_invoice, ""),
            ("Export PDF", self.export_pdf, ""),
            ("Search", self.open_search, ""),
            ("Delete", self.delete_invoice, "danger"),
            ("Clear", self.clear_form, "neutral"),
            ("Backup Data", self.backup_data, ""),
            ("Restore Data", self.restore_data, "danger"),
        ]
        for i, (text, slot, style) in enumerate(buttons):
            b = QPushButton(text)
            if style:
                b.setObjectName(style)
            b.clicked.connect(slot)
            act.addWidget(b, i // 5, i % 5)
        ll.addLayout(act)
        splitter.addWidget(left)

        # ---- right: live preview
        right = QGroupBox("Invoice Preview")
        rl = QVBoxLayout(right)
        self.preview = QTextBrowser()
        rl.addWidget(self.preview)
        splitter.addWidget(right)
        splitter.setSizes([760, 460])

        self.statusBar().showMessage(
            f"Database: {DB_PATH}   |   PDFs: {PDF_DIR}   |   Backups: {BACKUP_DIR}")

    def _refresh_seller_banner(self, seller: dict):
        self.seller_lbl.setText(seller.get("trade_name", APP_NAME))
        self.seller_lbl.setToolTip(
            f"{seller.get('address','')}\nGSTIN: {seller.get('gstin','')}\n"
            f"{seller.get('contact','')}")

    # ---------------- item grid helpers ----------------
    def add_row(self, item="", qty=1.0, rate=0.0, gst=18.0):
        self._loading = True
        r = self.table.rowCount()
        self.table.insertRow(r)
        vals = [item, f"{qty:g}", f"{rate:g}", f"{gst:g}",
                "0.00", "0.00", "0.00"]
        for c, v in enumerate(vals):
            it = QTableWidgetItem(v)
            if c >= self.COL_AMT:                    # calculated columns
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                it.setBackground(QColor("#eef3fb"))
            if c >= self.COL_QTY:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, c, it)
        self._loading = False
        self.recalc_row(r)

    def delete_row(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self.refresh_totals()

    def _cell_float(self, r, c, default=0.0) -> float:
        it = self.table.item(r, c)
        try:
            return float(it.text()) if it and it.text().strip() else default
        except ValueError:
            return default

    def on_item_changed(self, item):
        if self._loading:
            return
        if item.column() in (self.COL_QTY, self.COL_RATE, self.COL_GST):
            self.recalc_row(item.row())
        else:
            self.refresh_totals()

    def recalc_row(self, r):
        qty = self._cell_float(r, self.COL_QTY)
        rate = self._cell_float(r, self.COL_RATE)
        gst = self._cell_float(r, self.COL_GST)
        calc = self.logic.calc_line(qty, rate, gst)
        self._loading = True
        for col, key in ((self.COL_AMT, "amount"),
                         (self.COL_GSTAMT, "gst_amount"),
                         (self.COL_TOT, "total")):
            self.table.item(r, col).setText(f"{calc[key]:.2f}")
        self._loading = False
        self.refresh_totals()

    def collect_items(self) -> list:
        items = []
        for r in range(self.table.rowCount()):
            name_it = self.table.item(r, self.COL_ITEM)
            name = name_it.text().strip() if name_it else ""
            qty = self._cell_float(r, self.COL_QTY)
            rate = self._cell_float(r, self.COL_RATE)
            gst = self._cell_float(r, self.COL_GST)
            if not name and qty <= 0 and rate == 0:
                continue                              # skip fully blank rows
            calc = self.logic.calc_line(qty, rate, gst)
            items.append({"item_name": name, "qty": qty, "rate": rate,
                          "gst_pct": gst, **calc})
        return items

    def collect_invoice(self) -> dict:
        return {"invoice_no": self.inv_no.text().strip(),
                "invoice_date": self.inv_date.date().toString("dd-MM-yyyy"),
                "cust_name": self.c_name.text().strip(),
                "cust_address": self.c_addr.text().strip(),
                "cust_mobile": self.c_mob.text().strip(),
                "cust_gstin": self.c_gst.text().strip().upper()}

    def refresh_totals(self):
        items = self.collect_items()
        t = self.logic.calc_totals(items)
        self.l_sub.setText(f"{t['subtotal']:.2f}")
        self.l_gst.setText(f"{t['total_gst']:.2f}")
        self.l_ro.setText(f"{t['round_off']:+.2f}")
        self.l_grand.setText(f"{t['grand_total']:.2f}")
        self.update_preview(items, t)

    def update_preview(self, items=None, totals=None):
        if items is None:
            items = self.collect_items()
        if totals is None:
            totals = self.logic.calc_totals(items)
        inv = self.collect_invoice()
        inv.update(totals)
        html = self.logic.render_html(inv, items,
                                      self.logic.tax_summary(items),
                                      self.logic.db.get_seller())
        self.preview.setHtml(html)

    # ---------------- actions ----------------
    def new_invoice(self):
        self.clear_form(silent=True)
        self.inv_no.setText(self.logic.next_invoice_no())
        self.add_row()
        self.refresh_totals()
        self.statusBar().showMessage("New invoice ready.", 4000)

    def clear_form(self, silent=False):
        self.current_invoice_id = None
        for w in (self.c_name, self.c_addr, self.c_mob, self.c_gst):
            w.clear()
        self.inv_date.setDate(QDate.currentDate())
        self.table.setRowCount(0)
        self.refresh_totals()
        if not silent:
            self.inv_no.setText(self.logic.next_invoice_no())
            self.statusBar().showMessage("Form cleared.", 4000)

    def save_invoice(self):
        inv = self.collect_invoice()
        items = self.collect_items()
        inv_id, errors = self.logic.save_invoice(
            self.current_invoice_id, inv, items)
        if errors:
            QMessageBox.warning(self, "Cannot Save",
                                "Please fix the following:\n\n• " +
                                "\n• ".join(errors))
            return
        self.current_invoice_id = inv_id
        QMessageBox.information(self, APP_NAME,
                                f"Invoice {inv['invoice_no']} saved successfully.")
        self.refresh_totals()

    def open_search(self):
        dlg = SearchDialog(self.logic, self)
        if dlg.exec_() == QDialog.Accepted and dlg.selected_id:
            self.load_invoice(dlg.selected_id)

    def load_invoice(self, invoice_id: int):
        inv, items, _ = self.logic.load_invoice(invoice_id)
        if not inv:
            QMessageBox.warning(self, APP_NAME, "Invoice not found.")
            return
        self.current_invoice_id = invoice_id
        self.inv_no.setText(inv["invoice_no"])
        d = QDate.fromString(inv["invoice_date"], "dd-MM-yyyy")
        self.inv_date.setDate(d if d.isValid() else QDate.currentDate())
        self.c_name.setText(inv["cust_name"])
        self.c_addr.setText(inv["cust_address"] or "")
        self.c_mob.setText(inv["cust_mobile"] or "")
        self.c_gst.setText(inv["cust_gstin"] or "")
        self.table.setRowCount(0)
        for i in items:
            self.add_row(i["item_name"], i["qty"], i["rate"], i["gst_pct"])
        self.refresh_totals()
        self.statusBar().showMessage(
            f"Loaded invoice {inv['invoice_no']} for editing.", 5000)

    def delete_invoice(self):
        if not self.current_invoice_id:
            QMessageBox.information(self, APP_NAME,
                                    "Open a saved invoice first (Search) "
                                    "before deleting.")
            return
        no = self.inv_no.text()
        if QMessageBox.question(
                self, "Confirm Delete",
                f"Permanently delete invoice {no}?",
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.logic.delete_invoice(self.current_invoice_id)
            QMessageBox.information(self, APP_NAME, f"Invoice {no} deleted.")
            self.new_invoice()

    # ---- print / PDF ----
    def _prepared_data(self):
        inv = self.collect_invoice()
        items = self.collect_items()
        if not items:
            QMessageBox.warning(self, APP_NAME, "No items to output.")
            return None
        inv.update(self.logic.calc_totals(items))
        return inv, items, self.logic.tax_summary(items)

    def export_pdf(self):
        data = self._prepared_data()
        if not data:
            return
        inv, items, taxes = data
        try:
            path = self.logic.export_pdf(inv, items, taxes,
                                         self.logic.db.get_seller())
            QMessageBox.information(self, "PDF Exported",
                                    f"PDF saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "PDF Error", str(e))

    def print_invoice(self):
        data = self._prepared_data()
        if not data:
            return
        inv, items, taxes = data
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageSize(QPrinter.A4)
        dlg = QPrintDialog(printer, self)
        if dlg.exec_() == QDialog.Accepted:
            doc = self.preview.document().clone()
            doc.setHtml(self.logic.render_html(
                inv, items, taxes, self.logic.db.get_seller()))
            doc.print_(printer)

    # ---- backup / restore ----
    def backup_data(self):
        box = QMessageBox(self)
        box.setWindowTitle("Backup Data")
        box.setText("Choose backup destination:")
        default = box.addButton("Default (/backup folder)",
                                QMessageBox.AcceptRole)
        manual = box.addButton("Choose Location…", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.exec_()
        try:
            if box.clickedButton() is default:
                path = self.logic.backup_database()
            elif box.clickedButton() is manual:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                path, _ = QFileDialog.getSaveFileName(
                    self, "Save Backup As",
                    os.path.join(BACKUP_DIR, f"billing_backup_{stamp}.db"),
                    "SQLite Database (*.db *.sqlite)")
                if not path:
                    return
                path = self.logic.backup_database(path)
            else:
                return
            QMessageBox.information(self, "Backup Complete",
                                    f"Backup saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Backup Failed", str(e))

    def restore_data(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Backup File", BACKUP_DIR,
            "SQLite Database (*.db *.sqlite);;All Files (*.*)")
        if not path:
            return
        if QMessageBox.question(
                self, "Confirm Restore",
                "Restoring will REPLACE the current database.\n"
                "A safety backup of current data will be taken automatically.\n\n"
                "Continue?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            safety = self.logic.restore_database(path)
            QMessageBox.information(
                self, "Restore Complete",
                f"Database restored successfully.\n\n"
                f"Previous data was backed up to:\n{safety}")
            self._refresh_seller_banner(self.logic.db.get_seller())
            self.new_invoice()
        except Exception as e:
            QMessageBox.critical(self, "Restore Failed", str(e))

    # ---- seller ----
    def edit_seller(self):
        dlg = SellerDialog(self.logic.db.get_seller(), self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.data()
            if not data["trade_name"]:
                QMessageBox.warning(self, APP_NAME, "Trade name is required.")
                return
            self.logic.db.save_seller(data)
            self._refresh_seller_banner(data)
            self.refresh_totals()

    # ---- exit reminder ----
    def closeEvent(self, event):
        ans = QMessageBox.question(
            self, "Exit — Backup Reminder",
            "Do you want to take a backup before exiting?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes)
        if ans == QMessageBox.Cancel:
            event.ignore()
            return
        if ans == QMessageBox.Yes:
            self.backup_data()
        self.logic.db.close()
        event.accept()


# ============================================================================
#  BOOTSTRAP
# ============================================================================
def excepthook(exc_type, exc_value, exc_tb):
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        QMessageBox.critical(None, "Unexpected Error", msg[-1500:])
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def main():
    ensure_folders()
    sys.excepthook = excepthook
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(STYLE_SHEET)
    app.setFont(QFont("Segoe UI", 9))
    db = DatabaseManager()
    logic = BillingLogic(db)
    win = MainWindow(logic)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
