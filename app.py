"""
FCN Terminal v3.0 — Flask API Backend (Stateless)
Binance Structured Product Portfolio Manager

All endpoints are stateless — no Flask session.
Credentials are passed per-request in the JSON body.
"""

import os
import sys
import time
import hmac
import hashlib
import traceback
from urllib.parse import urlencode
from datetime import datetime

from flask import Flask, request, jsonify, render_template
import requests as http_requests

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder="static", template_folder="templates")

DEFAULT_POOL = float(os.environ.get("FCN_DEFAULT_POOL", "6000.0"))
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/114.0.0.0"
}


# ---------------------------------------------------------------------------
# Safe type conversion
# ---------------------------------------------------------------------------

def _safe_float(val, default=0.0):
    """Convert to float safely; return *default* on failure."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val, default=0):
    """Convert to int safely; return *default* on failure."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Credential helper
# ---------------------------------------------------------------------------

def _get_credentials(data: dict):
    """Extract and validate api_key / secret_key from request body."""
    api_key = (data.get("api_key") or "").strip()
    secret_key = (data.get("secret_key") or "").strip()
    if not api_key or not secret_key:
        return None, None
    return api_key, secret_key


# ---------------------------------------------------------------------------
# Binance low-level helpers
# ---------------------------------------------------------------------------

def _hmac_sign(query_string: str, secret: str) -> str:
    return hmac.new(secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()


def _binance_get(endpoint: str, params: dict, api_key: str, secret_key: str):
    """Signed GET against Binance SAPI / API."""
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 10000
    qs = urlencode(params)
    sig = _hmac_sign(qs, secret_key)
    headers = {**DEFAULT_HEADERS, "X-MBX-APIKEY": api_key}
    for node in ("https://api.binance.com", "https://api1.binance.com"):
        try:
            r = http_requests.get(
                f"{node}{endpoint}?{qs}&signature={sig}",
                headers=headers, timeout=8,
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    return None


def _binance_post(endpoint: str, params: dict, api_key: str, secret_key: str):
    """Signed POST against Binance SAPI."""
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 10000
    qs = urlencode(params)
    sig = _hmac_sign(qs, secret_key)
    headers = {**DEFAULT_HEADERS, "X-MBX-APIKEY": api_key}
    try:
        r = http_requests.post(
            f"https://api.binance.com{endpoint}?{qs}&signature={sig}",
            headers=headers, timeout=8,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Market data (public — no auth needed)
# ---------------------------------------------------------------------------

_eth_price_cache = {"price": 0.0, "ts": 0}


def get_eth_price() -> float:
    """Return cached ETH/USDT price (TTL 60 s)."""
    now = time.time()
    if now - _eth_price_cache["ts"] < 60 and _eth_price_cache["price"] > 0:
        return _eth_price_cache["price"]
    for url in (
        "https://fapi.binance.com/fapi/v1/ticker/price?symbol=ETHUSDT",
        "https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT",
    ):
        try:
            r = http_requests.get(url, headers=DEFAULT_HEADERS, timeout=5)
            if r.status_code == 200:
                price = float(r.json()["price"])
                _eth_price_cache.update(price=price, ts=now)
                return price
        except Exception:
            continue
    return _eth_price_cache["price"]  # stale is better than 0


def fetch_settlement_price(settle_ts: int) -> float:
    """
    Fetch the settlement-window average price for a given timestamp.
    *settle_ts* is in **milliseconds**.
    Binance rule: arithmetic mean of per-second prices during 07:30-08:00 UTC.
    We approximate with 1-min kline close averages.
    """
    try:
        settle_date = datetime.utcfromtimestamp(settle_ts / 1000)
        start_dt = settle_date.replace(hour=7, minute=30, second=0, microsecond=0)
        end_dt = settle_date.replace(hour=8, minute=0, second=0, microsecond=0)
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)

        r = http_requests.get(
            "https://api.binance.com/api/v3/klines",
            params={
                "symbol": "ETHUSDT",
                "interval": "1m",
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 100,
            },
            headers=DEFAULT_HEADERS,
            timeout=10,
        )
        if r.status_code == 200:
            klines = r.json()
            if klines:
                closes = [float(k[4]) for k in klines]
                return sum(closes) / len(closes)
    except Exception:
        pass
    return 0.0


# ---------------------------------------------------------------------------
# Account data helpers
# ---------------------------------------------------------------------------

def fetch_dual_investment_positions(api_key: str, secret_key: str):
    """
    Fetch Dual Investment positions from Binance DCI endpoint.
    Returns (total_value, positions_list)
    
    Endpoint: /sapi/v1/dci/product/positions
    """
    total_value = 0.0
    positions = []
    
    try:
        print("[DEBUG] fetch_dual_investment_positions called", file=sys.stderr)
        
        # Use DCI product positions endpoint
        endpoint = "/sapi/v1/dci/product/positions"
        data = _binance_get(endpoint, {"size": 100}, api_key, secret_key)
        print(f"[DEBUG] DCI response: {data}", file=sys.stderr)
        
        if not data or not isinstance(data, dict):
            print("[DEBUG] No data returned from DCI endpoint", file=sys.stderr)
            return round(total_value, 2), positions
        
        if "code" in data and data.get("code") != 0:
            print(f"[DEBUG] DCI returned error: {data.get('msg')}", file=sys.stderr)
            # Return empty instead of error - API key might be restricted
            return round(total_value, 2), positions
        
        # Extract list from response - DCI returns {'list': [...]}
        all_positions = data.get("list", [])
        if not isinstance(all_positions, list):
            print(f"[DEBUG] Unexpected list format: {type(all_positions)}", file=sys.stderr)
            return round(total_value, 2), positions
        
        print(f"[DEBUG] DCI returned {len(all_positions)} total positions", file=sys.stderr)
        
        # Get current ETH price for value calculation
        eth_price = get_eth_price()
        
        for item in all_positions:
            status = item.get("purchaseStatus", "")
            
            # Only include PURCHASE_SUCCESS (active) positions
            if status != "PURCHASE_SUCCESS":
                continue
            
            # Extract position details per Dan's spec
            product_id = str(item.get("id", "-"))
            invest_coin = item.get("investCoin", "")  # ETH or USDT
            subscription_amount = _safe_float(item.get("subscriptionAmount", 0))
            strike_price = _safe_float(item.get("strikePrice", 0))
            settle_ts = _safe_int(item.get("settleDate", 0))
            apr_raw = _safe_float(item.get("apr", 0))
            
            # Convert APR to percentage (Binance returns as decimal e.g. 0.0048)
            apr_pct = apr_raw * 100 if apr_raw < 1 else apr_raw
            
            # Calculate position value based on invest coin
            if invest_coin == "ETH":
                # ETH投入：金額 × 當前ETH價格
                value = subscription_amount * eth_price
            else:
                # USDT投入：直接就是USDT價值
                value = subscription_amount
            
            positions.append({
                "product_id": product_id,
                "invest_amount": round(subscription_amount, 4),
                "invest_coin": invest_coin,
                "strike_price": round(strike_price, 2),
                "settle_date": _fmt_date(settle_ts),
                "apr": round(apr_pct, 2),
                "value_usd": round(value, 2),
            })
            total_value += value
        
        print(f"[DEBUG] Active DCI positions: {len(positions)}, total value: {total_value}", file=sys.stderr)
    except Exception as e:
        print(f"[ERROR] fetch_dual_investment_positions exception: {e}", file=sys.stderr)
        traceback.print_exc()
    
    return round(total_value, 2), positions


def fetch_dual_investment_history(api_key: str, secret_key: str, cutoff_days: int = 30):
    """
    Fetch DCI historical settled positions.
    Returns (total_value, positions_list)
    """
    total_value = 0.0
    positions = []
    cutoff_ms = int((time.time() - cutoff_days * 86400) * 1000)
    
    try:
        print("[DEBUG] fetch_dual_investment_history called", file=sys.stderr)
        
        # Use DCI product positions endpoint with all statuses
        endpoint = "/sapi/v1/dci/product/positions"
        data = _binance_get(endpoint, {"size": 100}, api_key, secret_key)
        print(f"[DEBUG] DCI history response: {data}", file=sys.stderr)
        
        if not data or not isinstance(data, dict):
            print("[DEBUG] No data returned from DCI endpoint", file=sys.stderr)
            return round(total_value, 2), positions
        
        if "code" in data and data.get("code") != 0:
            print(f"[DEBUG] DCI returned error: {data.get('msg')}", file=sys.stderr)
            return round(total_value, 2), positions
        
        # Extract list from response
        all_positions = data.get("list", [])
        if not isinstance(all_positions, list):
            print(f"[DEBUG] Unexpected list format: {type(all_positions)}", file=sys.stderr)
            return round(total_value, 2), positions
        
        print(f"[DEBUG] DCI returned {len(all_positions)} total positions", file=sys.stderr)
        
        for item in all_positions:
            status = item.get("purchaseStatus", "")
            settle_ts = _safe_int(item.get("settleDate", 0))
            
            # Only include settled positions within cutoff
            if status not in ("SETTLED", "DELIVERED", "EXPIRED"):
                continue
            if settle_ts < cutoff_ms:
                continue
            
            # Extract position details
            product_id = str(item.get("id", "-"))
            invest_coin = item.get("investCoin", "")  # ETH or USDT
            subscription_amount = _safe_float(item.get("subscriptionAmount", 0))
            strike_price = _safe_float(item.get("strikePrice", 0))
            apr_raw = _safe_float(item.get("apr", 0))
            settle_date = _fmt_date(settle_ts)
            
            # Calculate duration (days between subscription and settlement)
            sub_ts = _safe_int(item.get("subscriptionTime", 0))
            dur_days = max(1, (settle_ts - sub_ts) // 86400000) if settle_ts > sub_ts else 7
            
            # Convert APR to percentage
            apr_pct = apr_raw * 100 if apr_raw < 1 else apr_raw
            
            positions.append({
                "pid": product_id,
                "invest_amount": round(subscription_amount, 4),
                "invest_coin": invest_coin,
                "strike": round(strike_price, 2),
                "settle_date": settle_date,
                "settle_ts": settle_ts,
                "apr": round(apr_pct, 2),
                "dur": dur_days,
            })
            total_value += subscription_amount if invest_coin == "USDT" else subscription_amount * strike_price
        
        print(f"[DEBUG] DCI history positions: {len(positions)}", file=sys.stderr)
    except Exception as e:
        print(f"[ERROR] fetch_dual_investment_history exception: {e}", file=sys.stderr)
        traceback.print_exc()
    
    return round(total_value, 2), positions


def calculate_dci_settlement(pos: dict, settle_price: float) -> dict:
    """
    Calculate DCI settlement result.
    
    DCI Rules:
    - If settle_price >= strike: return original coin + interest
    - If settle_price < strike: return converted coin + interest
    """
    invest_coin = pos.get("invest_coin", "")
    invest_amount = pos.get("invest_amount", 0)
    strike = pos.get("strike", 0)
    apr = pos.get("apr", 0)
    dur = pos.get("dur", 7)
    
    # Calculate interest
    interest_multiplier = 1 + (apr / 100 * dur / 365)
    
    if settle_price >= strike:
        # Not exercised - return original coin with interest
        if invest_coin == "USDT":
            scenario = "A"
            usdt_return = invest_amount * interest_multiplier
            eth_return = 0
        else:  # ETH invested, but price is high, so return USDT equivalent
            scenario = "A"
            usdt_return = invest_amount * strike * interest_multiplier
            eth_return = 0
    else:
        # Exercised - return converted coin with interest
        if invest_coin == "USDT":
            scenario = "B"
            usdt_return = 0
            eth_return = (invest_amount / strike) * interest_multiplier
        else:  # ETH invested
            scenario = "B"
            usdt_return = 0
            eth_return = invest_amount * interest_multiplier
    
    return {
        "scenario": scenario,
        "usdt_return": round(usdt_return, 2),
        "eth_return": round(eth_return, 6),
    }


def fetch_spot_balances(api_key: str, secret_key: str):
    """Return (usdt, eth) spot balances."""
    usdt = eth = 0.0
    
    try:
        print(f"[DEBUG] fetch_spot_balances called with api_key prefix: {api_key[:8]}..." if api_key else "[DEBUG] fetch_spot_balances called with empty api_key", file=sys.stderr)

        # Method 1: sapi/v3/asset/getUserAsset (POST)
        print("[DEBUG] Trying Method 1: sapi/v3/asset/getUserAsset", file=sys.stderr)
        data = _binance_post(
            "/sapi/v3/asset/getUserAsset",
            {"needBtcValuation": "false"},
            api_key, secret_key,
        )
        print(f"[DEBUG] Method 1 response: {data}", file=sys.stderr)
        
        # Check for API error (e.g., invalid key, IP restrictions)
        if data and isinstance(data, dict) and "code" in data:
            error_code = data.get('code')
            error_msg = data.get('msg', 'Unknown error')
            print(f"[DEBUG] Method 1 returned error code: {error_code}, msg: {error_msg}", file=sys.stderr)
            # Return 0,0 instead of raising error - let the UI show empty data
            return 0.0, 0.0
        
        if data and isinstance(data, list):
            print(f"[DEBUG] Method 1 returned list with {len(data)} assets", file=sys.stderr)
            for b in data:
                asset = b.get("asset", "")
                total = _safe_float(b.get("free", 0)) + _safe_float(b.get("locked", 0))
                print(f"[DEBUG] Asset: {asset}, Total: {total}", file=sys.stderr)
                if asset == "USDT":
                    usdt = total
                elif asset == "ETH":
                    eth = total
            print(f"[DEBUG] Method 1 result - USDT: {usdt}, ETH: {eth}", file=sys.stderr)
        else:
            print(f"[DEBUG] Method 1 failed or returned unexpected format", file=sys.stderr)

        # Method 2 fallback: api/v3/account
        if usdt == 0.0 and eth == 0.0:
            print("[DEBUG] Method 1 returned zero balances, trying Method 2: api/v3/account", file=sys.stderr)
            data2 = _binance_get("/api/v3/account", {}, api_key, secret_key)
            print(f"[DEBUG] Method 2 response: {data2}", file=sys.stderr)
            
            if data2 and isinstance(data2, dict):
                if "code" in data2:
                    error_code = data2.get('code')
                    error_msg = data2.get('msg', 'Unknown error')
                    print(f"[DEBUG] Method 2 returned error code: {error_code}, msg: {error_msg}", file=sys.stderr)
                    # Return 0,0 instead of raising error
                    return 0.0, 0.0
                elif "balances" in data2:
                    balances = data2.get("balances", [])
                    print(f"[DEBUG] Method 2 returned {len(balances)} balances", file=sys.stderr)
                    for b in balances:
                        asset = b.get("asset", "")
                        total = _safe_float(b.get("free", 0)) + _safe_float(b.get("locked", 0))
                        if total > 0:
                            print(f"[DEBUG] Asset: {asset}, Total: {total}", file=sys.stderr)
                        if asset == "USDT":
                            usdt = total
                        elif asset == "ETH":
                            eth = total
                    print(f"[DEBUG] Method 2 result - USDT: {usdt}, ETH: {eth}", file=sys.stderr)
                else:
                    print(f"[DEBUG] Method 2 returned unexpected format: {type(data2)}", file=sys.stderr)
            else:
                print(f"[DEBUG] Method 2 failed - response type: {type(data2)}", file=sys.stderr)
        else:
            print(f"[DEBUG] Skipping Method 2, already got balances from Method 1", file=sys.stderr)
    except Exception as e:
        print(f"[ERROR] fetch_spot_balances exception: {e}", file=sys.stderr)
        traceback.print_exc()
    
    print(f"[DEBUG] Final result - USDT: {usdt}, ETH: {eth}", file=sys.stderr)
    return usdt, eth


# ---------------------------------------------------------------------------
# Position helpers
# ---------------------------------------------------------------------------

def _extract_list(data):
    if not data:
        return []
    if isinstance(data, list):
        return data
    d = data.get("data", data)
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        return d.get("list", [])
    return data.get("list", [])


def _fmt_date(ts) -> str | None:
    try:
        if not ts:
            return None
        if isinstance(ts, str):
            if "T" in ts:
                return ts.split("T")[0]
            if len(ts) >= 10 and ts[4] == "-" and ts[7] == "-":
                return ts[:10]
            ts = int(float(ts))
        ts_int = int(ts)
        if ts_int > 1_000_000_000_000:
            ts_int //= 1000
        if ts_int > 0:
            return datetime.utcfromtimestamp(ts_int).strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def _assign_phases(items: list) -> list:
    sorted_items = sorted(items, key=lambda x: x.get("sub_ts", 0))
    for i, item in enumerate(sorted_items):
        item["phase"] = f"P{i + 1}"
    return sorted_items


def calculate_settlement(amt: float, strike: float, ko: float,
                         apr: float, dur: int, settle_price: float):
    """
    Core FCN settlement calculation.

    `apr` is in **percentage** form (e.g. 31.41 means 31.41 %).
    Returns ``(scenario, usdt_return, eth_return)``.

    Binance FCN rules (verified with user 2026-03-28)
    ------------------
    * **KO**  settle_price >= ko        → principal + interest (USDT)
    * **S1**  settle_price < strike    → 100 % converted to ETH at strike
    * **S2**  strike <= settle_price < ko → 50/50 split (USDT + ETH at strike)
    """
    if settle_price <= 0:
        return "S2", 0.0, 0.0

    if settle_price >= ko:
        # KO — principal + interest returned in USDT
        scenario = "KO"
        usdt_return = amt * (1 + apr / 100 * dur / 365)
        eth_return = 0.0
    elif settle_price < strike:
        # S1 — fully converted to ETH at strike price (settle < strike)
        scenario = "S1"
        usdt_return = 0.0
        eth_return = amt / strike if strike > 0 else 0.0
    else:
        # S2 — 50/50 fixed split (strike <= settle < ko)
        scenario = "S2"
        usdt_return = amt * 0.5
        eth_return = (amt * 0.5) / strike if strike > 0 else 0.0

    return scenario, round(usdt_return, 2), round(eth_return, 6)


def sync_positions(api_key: str, secret_key: str, cutoff_days: int = 8):
    """Fetch active + recently-settled positions from Binance."""
    active = []
    settled = []
    seen = set()
    cutoff_ms = int((time.time() - cutoff_days * 86400) * 1000)

    try:
        data = _binance_get(
            "/sapi/v1/accumulator/product/position/list",
            {"pageSize": 100, "pageIndex": 1},
            api_key, secret_key,
        )
        
        # Check for API error
        if data and isinstance(data, dict) and "code" in data:
            error_code = data.get('code')
            error_msg = data.get('msg', 'Unknown error')
            print(f"[DEBUG] sync_positions returned error code: {error_code}, msg: {error_msg}", file=sys.stderr)
            # Return empty instead of error
            return active, settled
        
    except Exception as e:
        print(f"[ERROR] sync_positions exception: {e}", file=sys.stderr)
        return active, settled

    for p in _extract_list(data):
        raw_pid = p.get("positionId", p.get("productId", "-"))
        try:
            pid = str(int(float(str(raw_pid))))
        except (ValueError, TypeError):
            pid = str(raw_pid)
        if pid in seen:
            continue
        seen.add(pid)

        status = str(p.get("status", p.get("purchaseStatus", ""))).upper()
        settle_ts = _safe_int(p.get("settleDate", p.get("deliveryDate", 0)))
        amt = _safe_float(p.get("depositAmount", p.get("subscriptionAmount", 0)))
        strike = _safe_float(p.get("strikePrice", p.get("targetPrice", 0)))
        ko_price = _safe_float(p.get("knockOutPrice", p.get("knockoutPrice", 0)))
        # Binance returns APR as decimal (e.g. 0.3141) — convert to %
        apr_raw = _safe_float(p.get("knockOutApr", p.get("apr", 0.3)), default=0.3)
        apr_pct = apr_raw * 100 if apr_raw < 1 else apr_raw  # safety
        dur = _safe_int(p.get("duration", 1), default=1)
        sub_ts = _safe_int(p.get("subscriptionTime", 0))

        item = {
            "pid": pid,
            "amt": amt,
            "strike": strike,
            "ko": ko_price,
            "apr": round(apr_pct, 2),
            "dur": dur,
            "sub_ts": sub_ts,
            "settle_ts": settle_ts,
            "settle_date": _fmt_date(settle_ts),
        }

        if status in ("SETTLED", "KNOCK_OUT", "EXPIRED") and settle_ts > 0:
            if settle_ts >= cutoff_ms:
                settled.append(item)
        else:
            active.append(item)

    active = _assign_phases(active)
    settled = _assign_phases(settled)
    return active, settled


# ===================================================================
# API Routes
# ===================================================================

# ---- Serve frontend ----
@app.route("/")
def index():
    return render_template("index.html")


# ---- POST /api/connect ----
@app.route("/api/connect", methods=["POST"])
def api_connect():
    """Validate Binance credentials (stateless — no session storage)."""
    try:
        body = request.get_json(silent=True) or {}
        api_key, secret_key = _get_credentials(body)

        if not api_key or not secret_key:
            return jsonify({"success": False, "error": "api_key and secret_key required"}), 400

        # Quick validation — try fetching balances
        usdt, eth = fetch_spot_balances(api_key, secret_key)
        dual_total, _ = fetch_dual_investment_positions(api_key, secret_key)
        eth_price = get_eth_price()

        return jsonify({
            "success": True,
            "eth_price": round(eth_price, 2),
            "dual_total": dual_total,
            "spot_usdt": round(usdt, 2),
            "spot_eth": round(eth, 6),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Internal error: {str(e)}"}), 500


# ---- POST /api/positions ----
@app.route("/api/positions", methods=["POST"])
def api_positions():
    """Fetch positions from Binance (active + settled)."""
    try:
        body = request.get_json(silent=True) or {}
        api_key, secret_key = _get_credentials(body)
        if not api_key or not secret_key:
            return jsonify({"error": "Missing credentials"}), 401

        cutoff_days = int(body.get("cutoff_days", 8))
        cutoff_days = max(1, min(cutoff_days, 90))

        spot_usdt, spot_eth = fetch_spot_balances(api_key, secret_key)
        dual_total, dual_positions = fetch_dual_investment_positions(api_key, secret_key)
        active, settled = sync_positions(api_key, secret_key, cutoff_days)
        eth_price = get_eth_price()

        return jsonify({
            "active": active,
            "settled": settled,
            "spot_usdt": round(spot_usdt, 2),
            "spot_eth": round(spot_eth, 6),
            "dual_investment": {"total_principal": dual_total, "positions": dual_positions},
            "eth_price": round(eth_price, 2),
            "dual_total": dual_total,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Internal error: {str(e)}"}), 500


# ---- POST /api/calculate ----
@app.route("/api/calculate", methods=["POST"])
def api_calculate():
    """
    Calculate settlement results for a batch of positions.

    Body::

        {
            "api_key": "...",
            "secret_key": "...",
            "batch": [ ...position objects... ],
            "settle_price": 2157.87
        }

    Each position in *batch* must have: pid, phase, amt, strike, ko, apr, dur.
    ``apr`` in percentage form (e.g. 31.41).
    """
    try:
        body = request.get_json(silent=True) or {}
        api_key, secret_key = _get_credentials(body)
        if not api_key or not secret_key:
            return jsonify({"error": "Missing credentials"}), 401

        batch = body.get("batch", [])
        settle_price = _safe_float(body.get("settle_price", 0))

        if not batch:
            return jsonify({"success": False, "error": "batch is empty"}), 400
        if settle_price <= 0:
            return jsonify({"success": False, "error": "settle_price must be > 0"}), 400

        results = []
        total_reinvest = 0.0
        total_topup = 0.0
        total_eth = 0.0
        total_eth_cost_numerator = 0.0  # sum of USDT spent on ETH

        for pos in batch:
            amt = _safe_float(pos.get("amt", 0))
            strike = _safe_float(pos.get("strike", 0))
            ko = _safe_float(pos.get("ko", 0))
            apr = _safe_float(pos.get("apr", 0))
            dur = _safe_int(pos.get("dur", 1), default=1)
            pid = str(pos.get("pid", ""))
            phase = str(pos.get("phase", ""))

            scenario, usdt_return, eth_return = calculate_settlement(
                amt, strike, ko, apr, dur, settle_price
            )

            if scenario == "KO":
                reinvest = usdt_return
                topup = 0.0
            elif scenario == "S1":
                reinvest = 0.0
                topup = amt
            else:  # S2
                reinvest = usdt_return
                topup = amt - usdt_return

            reinvest = round(reinvest, 2)
            topup = round(topup, 2)

            total_reinvest += reinvest
            total_topup += topup
            total_eth += eth_return
            if eth_return > 0:
                total_eth_cost_numerator += (amt - usdt_return)

            results.append({
                "pid": pid,
                "phase": phase,
                "scenario": scenario,
                "amt": amt,
                "usdt_return": usdt_return,
                "eth_return": eth_return,
            })

        total_next = round(total_reinvest + total_topup, 2)
        total_eth_cost = (
            round(total_eth_cost_numerator / total_eth, 2)
            if total_eth > 0 else 0.0
        )

        return jsonify({
            "results": results,
            "summary": {
                "total_reinvest": round(total_reinvest, 2),
                "total_topup": round(total_topup, 2),
                "total_next": total_next,
                "total_eth": round(total_eth, 6),
                "total_eth_cost": total_eth_cost,
            },
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Internal error: {str(e)}"}), 500


# ---- POST /api/settlement-price ----
@app.route("/api/settlement-price", methods=["POST"])
def api_settlement_price():
    """Get the settlement window price for a given millisecond timestamp."""
    try:
        body = request.get_json(silent=True) or {}
        timestamp = int(body.get("timestamp", 0))

        if timestamp <= 0:
            return jsonify({"price": 0, "source": "invalid_timestamp"}), 400

        price = fetch_settlement_price(timestamp)
        if price > 0:
            return jsonify({"price": round(price, 2), "source": "binance_kline"})
        return jsonify({"price": 0, "source": "unavailable"}), 404
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Internal error: {str(e)}"}), 500


# ---- GET /api/eth-price ----
@app.route("/api/eth-price")
def api_eth_price():
    """Current ETH/USDT price (public, no auth needed)."""
    try:
        price = get_eth_price()
        return jsonify({"price": round(price, 2)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Internal error: {str(e)}"}), 500


# ---- POST /api/history ----
@app.route("/api/history", methods=["POST"])
def api_history():
    """Fetch historical settled positions with date range - supports FCN and DCI."""
    try:
        body = request.get_json(silent=True) or {}
        api_key, secret_key = _get_credentials(body)
        if not api_key or not secret_key:
            return jsonify({"error": "Missing credentials"}), 401
        
        # Get parameters
        product_type = body.get("type", "fcn")  # 'fcn' or 'dci'
        settle_date = body.get("settle_date")   # Specific date to query
        
        # Default 30 days
        cutoff_days = 30
        
        if product_type == "fcn":
            # Get FCN settled positions
            _, settled = sync_positions(api_key, secret_key, cutoff_days)
            
            # Group by settle_date
            date_groups = {}
            for pos in settled:
                d = pos.get("settle_date")
                if d:
                    if d not in date_groups:
                        date_groups[d] = []
                    date_groups[d].append(pos)
            
            # Get all available dates (sorted)
            available_dates = sorted(date_groups.keys(), reverse=True)
            
            # If specific date requested, filter
            if settle_date and settle_date in date_groups:
                result_positions = date_groups[settle_date]
            elif available_dates:
                # Default to latest date
                settle_date = available_dates[0]
                result_positions = date_groups[settle_date]
            else:
                result_positions = []
                settle_date = None
            
            # Auto-calculate settlement results
            calculated = []
            for pos in result_positions:
                settle_ts = pos.get("settle_ts", 0)
                settle_price = 0
                
                # Try to fetch settlement price
                if settle_ts > 0:
                    try:
                        settle_price = fetch_settlement_price(settle_ts)
                    except:
                        pass
                
                # Fallback: use strike price if fetch failed
                if settle_price <= 0:
                    settle_price = pos.get("strike", 0)
                
                if settle_price > 0:
                    scenario, usdt_return, eth_return = calculate_settlement(
                        pos["amt"], pos["strike"], pos["ko"], pos["apr"], pos["dur"], settle_price
                    )
                    pos["scenario"] = scenario
                    pos["usdt_return"] = usdt_return
                    pos["eth_return"] = eth_return
                    pos["actual_settle_price"] = settle_price
                calculated.append(pos)
            
            return jsonify({
                "type": "fcn",
                "settled": calculated,
                "count": len(calculated),
                "settle_date": settle_date,
                "available_dates": available_dates,
            })
            
        elif product_type == "dci":
            # Get DCI settled positions
            _, dci_positions = fetch_dual_investment_history(api_key, secret_key, cutoff_days)
            
            # Group by settle_date
            date_groups = {}
            for pos in dci_positions:
                d = pos.get("settle_date")
                if d:
                    if d not in date_groups:
                        date_groups[d] = []
                    date_groups[d].append(pos)
            
            # Get all available dates (sorted)
            available_dates = sorted(date_groups.keys(), reverse=True)
            
            # If specific date requested, filter
            if settle_date and settle_date in date_groups:
                result_positions = date_groups[settle_date]
            elif available_dates:
                # Default to latest date
                settle_date = available_dates[0]
                result_positions = date_groups[settle_date]
            else:
                result_positions = []
                settle_date = None
            
            # Auto-calculate DCI settlement results
            calculated = []
            for pos in result_positions:
                settle_ts = pos.get("settle_ts", 0)
                settle_price = 0
                
                # Try to fetch settlement price
                if settle_ts > 0:
                    try:
                        settle_price = fetch_settlement_price(settle_ts)
                    except:
                        pass
                
                # Fallback: use strike price if fetch failed
                if settle_price <= 0:
                    settle_price = pos.get("strike", 0)
                
                if settle_price > 0:
                    result = calculate_dci_settlement(pos, settle_price)
                    pos.update(result)
                    pos["actual_settle_price"] = settle_price
                calculated.append(pos)
            
            return jsonify({
                "type": "dci",
                "settled": calculated,
                "count": len(calculated),
                "settle_date": settle_date,
                "available_dates": available_dates,
            })
        
        else:
            return jsonify({"error": "Invalid type. Use 'fcn' or 'dci'"}), 400
            
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Internal error: {str(e)}"}), 500


# ---- POST /api/sync ----
@app.route("/api/sync", methods=["POST"])
def api_sync():
    """Lightweight sync: refresh balances + ETH price only."""
    try:
        body = request.get_json(silent=True) or {}
        api_key, secret_key = _get_credentials(body)
        if not api_key or not secret_key:
            return jsonify({"error": "Missing credentials"}), 401

        spot_usdt, spot_eth = fetch_spot_balances(api_key, secret_key)
        dual_total, dual_positions = fetch_dual_investment_positions(api_key, secret_key)
        eth_price = get_eth_price()
        return jsonify({
            "spot_usdt": round(spot_usdt, 2),
            "spot_eth": round(spot_eth, 6),
            "dual_investment": {"total_principal": dual_total, "positions": dual_positions},
            "eth_price": round(eth_price, 2),
            "dual_total": dual_total,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Internal error: {str(e)}"}), 500


# ===================================================================
# Main
# ===================================================================
if __name__ == "__main__":
    print("FCN Terminal v3.0 starting...", file=sys.stderr)
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print(f"  Listening on 0.0.0.0:{port} (debug={debug})", file=sys.stderr)
    app.run(host="0.0.0.0", port=port, debug=debug)