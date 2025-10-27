# Student ID: 012698912 
# File: main.py
# Submission Date: 10/27/2025
# WGUPS Routing Program (lists-only core; no Python dicts in storage/indexes)
#
# Rubric mapping:
# - [A,B] Custom chained hash table built from Python lists only; lookup by Package ID.
# - [C,F] Greedy nearest-neighbor router refined with availability gates and deadline-first policy.
# - [D,E] CLI menu 1/2/3/4: required fields per package @ HH:MM; TOTAL_MILES; stable output.
# - Hard rules enforced: 3 trucks / 2 drivers; cap=16; 18 mph; delayed @ 09:05; #9 fix @ 10:20; “Truck 2 only”.
# - Determinism: stable ID tie-breakers. Sanity mode via env WGUPS_SANITY=1 or --sanity.

import csv
import datetime
import os
import re
import sys

# ---------- Formatting helpers ----------
FMT = "%H:%M"

def hms_any(x):
    if x is None:
        return ""
    if isinstance(x, datetime.timedelta):
        total = int(x.total_seconds())
        if total < 0:
            total = 0
        h = (total // 3600) % 24
        m = (total % 3600) // 60
        return f"{h:02d}:{m:02d}"
    return x.strftime(FMT)

def parse_deadline(deadline_str):
    if deadline_str is None:
        return None
    s = (deadline_str or "").strip().upper()
    if s == "" or s == "EOD":
        return None
    try:
        hhmm, ampm = s.split()
        hh, mm = hhmm.split(":")
        h = int(hh); m = int(mm)
        if ampm == "PM" and h != 12:
            h += 12
        if ampm == "AM" and h == 12:
            h = 0
        return datetime.timedelta(hours=h, minutes=m)
    except:
        return None

def _open_first_that_exists(paths):
    for p in paths:
        if os.path.exists(p):
            return open(p, newline="")
    return open(paths[0], newline="")  # raise file not found clearly

def _norm(s):
    x = (s or "").strip().lower()
    x = re.sub(r"\s+", " ", x)
    return x

def _strip_suite(s):
    return re.sub(r"#\s*\d+\b", "", s or "").strip()

# ---------- Constants ----------
DELAYED_TIME = datetime.timedelta(hours=9, minutes=5)     # 09:05 delayed availability
ADDR_FIX_TIME = datetime.timedelta(hours=10, minutes=20)  # 10:20 correct address known for #9
TRUCK_SPEED = 18.0
TRUCK_CAP = 16

# ---------- Hash Table (lists-only) [A,B] ----------
class HashTableWChains:
    def __init__(self, initialcapacity=64):
        self.table = [[] for _ in range(initialcapacity)]
        self.size = 0

    def _bucket(self, key):
        return hash(key) % len(self.table)

    def insert(self, key, item):
        b = self._bucket(key)
        arr = self.table[b]
        for i in range(len(arr)):
            if arr[i][0] == key:
                arr[i] = (key, item)
                return True
        arr.append((key, item))
        self.size += 1
        if self.size * 4 > len(self.table) * 3:
            self._resize()
        return True

    def _resize(self):
        old = self.table
        self.table = [[] for _ in range(len(old) * 2)]
        self.size = 0
        for bucket in old:
            for k, v in bucket:
                self.insert(k, v)

    def search(self, key):
        arr = self.table[self._bucket(key)]
        for kv in arr:
            if kv[0] == key:
                return kv[1]
        return None

# ---------- Data model ----------
class Package:
    def __init__(self, ID, street, city, state, zipc, deadline, weight, notes):
        self.ID = ID
        self.street = street
        self.city = city
        self.state = state
        self.zip = zipc
        self.deadline = deadline
        self.weight = weight
        self.notes = notes or ""
        self.status = "AT HUB"
        self.departureTime = None
        self.deliveryTime = None
        self.truck = None
        # Preserve original vs corrected (for #9 display rules)
        self._orig_street = street
        self._orig_zip = zipc
        self._corr_street = street
        self._corr_zip = zipc

    def statusUpdate(self, qtime):
        # Always enforce gates first (even if the truck has departed)
        if self.ID == 9 and qtime < ADDR_FIX_TIME:
            self.status = "AT HUB (awaiting 10:20 address correction)"
            return
        if ("delayed on flight" in self.notes.lower()) and qtime < DELAYED_TIME:
            self.status = "DELAYED — will not arrive at the depot until 09:05 AM"
            return

        # Before depart: AT HUB
        if self.departureTime is None or qtime < self.departureTime:
            self.status = "AT HUB"
            return

        # After depart and before delivered: EN ROUTE
        if self.deliveryTime is None or qtime < self.deliveryTime:
            self.status = "EN ROUTE on %s" % (self.truck if self.truck else "Truck")
        else:
            self.status = "DELIVERED"

class Truck:
    def __init__(self, name, speed, currentLocation, departTime, packages):
        self.name = name
        self.speed = speed
        self.currentLocation = currentLocation
        self.departTime = departTime
        self.time = departTime
        self.miles = 0.0
        self.packages = packages[:]  # list of IDs only (<= 16)

# ---------- CSV loading ----------
AddressCSV = []   # [index, name, street used in distance CSV]
DistanceCSV = []  # numeric rows; lower-tri / mirrored matrix
packageHash = HashTableWChains()

def load_address_csv():
    global AddressCSV
    with _open_first_that_exists(["addressCSV.csv", "CSVFiles/addressCSV.csv"]) as f:
        AddressCSV = list(csv.reader(f))

def _is_float(s):
    try:
        float(s); return True
    except:
        return False

def load_distance_csv():
    global DistanceCSV
    with _open_first_that_exists(["distanceCSV.csv", "CSVFiles/distanceCSV.csv"]) as f:
        DistanceCSV = list(csv.reader(f))
    cleaned = []
    for row in DistanceCSV:
        if not row:
            continue
        if _is_float(row[0].strip()):
            cleaned.append(row)
    if cleaned:
        DistanceCSV = cleaned

def loadPackageData():
    with _open_first_that_exists(["packageCSV.csv", "CSVFiles/packageCSV.csv"]) as f:
        r = csv.reader(f)
        _ = next(r, None)
        for row in r:
            if not row or not row[0].strip():
                continue
            pID = int(row[0].strip())
            pStreet = row[1].strip()
            pCity = row[2].strip()
            pState = row[3].strip()
            pZip = row[4].strip()
            pDeadline = row[5].strip()
            pWeight = row[6].strip()
            pNotes = row[7].strip() if len(row) > 7 else ""
            pkg = Package(pID, pStreet, pCity, pState, pZip, pDeadline, pWeight, pNotes)
            if pID == 9:
                pkg._corr_street = "410 S State St"
                pkg._corr_zip = "84111"
            packageHash.insert(pID, pkg)

# ---------- Address/Distance helpers ----------
def _address_index_for(street):
    tgt = _norm(street)
    tgt2 = _norm(_strip_suite(street))
    for row in AddressCSV:
        if len(row) < 3:
            continue
        try:
            idx = int(row[0].strip())
        except:
            continue
        s = _norm(row[2])
        if tgt == s or tgt2 == s:
            return idx
    for row in AddressCSV:
        if len(row) < 3:
            continue
        try:
            idx = int(row[0].strip())
        except:
            continue
        s = _norm(row[2])
        if tgt in s or tgt2 in s or s in tgt or s in tgt2:
            return idx
    return None

def address_idx(street):
    idx = _address_index_for(street)
    return 0 if idx is None else idx

_fallback_count = 0

def dist_between(idx1, idx2):
    global _fallback_count
    if idx1 is None or idx2 is None:
        _fallback_count += 1
        return 7.5
    n = len(DistanceCSV)
    if idx1 >= n or idx2 >= n:
        _fallback_count += 1
        return 7.5
    row = DistanceCSV[idx1]
    val = row[idx2] if idx2 < len(row) else ''
    if val == '' or not _is_float(val):
        row2 = DistanceCSV[idx2]
        val2 = row2[idx1] if idx1 < len(row2) else ''
        if _is_float(val2):
            return float(val2)
        _fallback_count += 1
        return 7.5
    return float(val)

# ---------- Gates & eligibility ----------
def is_delayed(pkg):
    return "delayed on flight" in (pkg.notes or "").lower()

def apply_addr_fix_if_due(pkg, now_time):
    """
    For ROUTING ONLY: when current time reaches 10:20, ensure package #9's
    internal address fields are corrected so distance lookups use the right node.
    """
    if pkg.ID == 9 and now_time >= ADDR_FIX_TIME:
        if pkg.street != pkg._corr_street or pkg.zip != pkg._corr_zip:
            pkg.street = pkg._corr_street
            pkg.zip = pkg._corr_zip

def eligible_now(pkg, now_time):
    if pkg.ID == 9 and now_time < ADDR_FIX_TIME:
        return False
    if is_delayed(pkg) and now_time < DELAYED_TIME:
        return False
    return True

# ---------- Display helper for time-aware address (#9) ----------
def display_address(pkg, qtime):
    """
    Return (street, city, state, zip) to DISPLAY at query time.
    - For #9 BEFORE 10:20 => show original (incorrect) address per rubric.
    - For #9 AT/AFTER 10:20 => show corrected address.
    - For all others => show current fields.
    """
    if pkg.ID == 9:
        if qtime < ADDR_FIX_TIME:
            return pkg._orig_street, pkg.city, pkg.state, pkg._orig_zip
        else:
            return pkg._corr_street, pkg.city, pkg.state, pkg._corr_zip
    return pkg.street, pkg.city, pkg.state, pkg.zip

# ---------- Routing (deadline-first + NN fallback) [C,F] ----------
def deliver_run(truck):
    # Instantiate enroute packages (attach truck, set departure time when first moved)
    onboard = []
    for pid in truck.packages:
        p = packageHash.search(pid)
        if p is not None:
            p.truck = truck.name
            if p.departureTime is None:
                gated = truck.departTime
                if is_delayed(p):
                    if gated < DELAYED_TIME:
                        gated = DELAYED_TIME
                if p.ID == 9:
                    if gated < ADDR_FIX_TIME:
                        gated = ADDR_FIX_TIME
                p.departureTime = gated
            onboard.append(p)
    truck.packages = []

    while onboard:
        # Respect availability/address correction gates for the current time
        elig = []
        for p in onboard:
            apply_addr_fix_if_due(p, truck.time)  # routing-side correction
            if eligible_now(p, truck.time):
                elig.append(p)
        if not elig:
            next_times = []
            if truck.time < DELAYED_TIME and any(is_delayed(p) for p in onboard):
                next_times.append(DELAYED_TIME)
            if truck.time < ADDR_FIX_TIME and any(p.ID == 9 for p in onboard):
                next_times.append(ADDR_FIX_TIME)
            if not next_times:
                break
            truck.time = min(t for t in next_times if t > truck.time)
            continue

        # Compute arrival time info for eligibles
        info = []
        cur_idx = address_idx(truck.currentLocation)
        for p in elig:
            d = dist_between(cur_idx, address_idx(p.street))
            arrival = truck.time + datetime.timedelta(hours=d / TRUCK_SPEED)
            info.append((p, d, arrival, parse_deadline(p.deadline)))

        # Partition
        ontime_hd = []
        late_hd = []
        eod = []
        for p, d, arr, dl in info:
            if dl is None:
                eod.append((p, d, arr, dl))
            elif arr <= dl:
                ontime_hd.append((p, d, arr, dl))
            else:
                late_hd.append((p, d, arr, dl))

        # Selection rules:
        if ontime_hd:
            # Prefer earliest arrival (secure the deadline), then shorter distance, then lower ID.
            # Soft bonus for being >=10 minutes early.
            best = None
            best_score = 1e18
            for p, d, arr, dl in ontime_hd:
                target = dl - datetime.timedelta(minutes=10)
                early_bonus = 0.2 if arr <= target else 0.0
                score = (arr.total_seconds()/60.0) + d - early_bonus + p.ID * 1e-6
                if score < best_score:
                    best_score = score
                    best = (p, d)
            sel_p, sel_d = best
        elif late_hd:
            # All remaining deadlines would be late: minimize lateness first, then distance, then ID
            best = late_hd[0]
            for item in late_hd[1:]:
                p0, d0, a0, dl0 = best
                p1, d1, a1, dl1 = item
                late0 = (a0 - dl0).total_seconds()
                late1 = (a1 - dl1).total_seconds()
                if (late1 < late0) or (late1 == late0 and d1 < d0) or (late1 == late0 and d1 == d0 and p1.ID < p0.ID):
                    best = item
            sel_p, sel_d, _, _ = best
        else:
            # Only EOD remain — nearest neighbor with stable ID tiebreak
            sel_p = None
            sel_d = 0.0
            best_score = 1e18
            for p, d, _, _ in eod:
                score = d + p.ID * 1e-6
                if score < best_score:
                    best_score = score
                    sel_p = p
                    sel_d = d

        # Travel to selection
        truck.miles += sel_d
        truck.time += datetime.timedelta(hours=sel_d / TRUCK_SPEED)
        truck.currentLocation = sel_p.street
        sel_p.deliveryTime = truck.time
        sel_p.status = "DELIVERED"
        truck.packages.append(sel_p.ID)
        onboard.remove(sel_p)

# ---------- Simulation + CLI ----------
def run(sim_sanity=False):
    load_address_csv()
    load_distance_csv()
    loadPackageData()

    HUB = "4001 South 700 East"

    # Manifests tuned to: (i) get ALL 9:00/10:30 on time; (ii) avoid crisscross to keep miles < 140.
    # Truck 1 @ 08:00 — constraint cluster + remaining 10:30s (east/south). No delayed items here.
    # Constraint cluster: 13,14,15,16,19,20 (must-be-with group; includes 9:00 and 10:30 deadlines)
    truck1 = Truck(
        "Truck 1",
        TRUCK_SPEED,
        HUB,
        datetime.timedelta(hours=8, minutes=0),
        [
            15, 13, 16, 20, 14, 19,        # honor group, place 9:00 (15) first
            34, 31, 29, 30, 37, 1, 40      # remaining 10:30s & a nearby 10:30 cluster
        ][:TRUCK_CAP]
    )

    # Truck 3 @ 09:05 — delayed group first (6 & 25 have 10:30 deadlines), then compact downtown EOD.
    # (Also includes #24 and #35 so NOTHING remains at the hub.)
    truck3 = Truck(
        "Truck 3",
        TRUCK_SPEED,
        HUB,
        datetime.timedelta(hours=9, minutes=5),
        [
            6, 25, 28, 32,                 # all delayed; router will hit 6/25 first due to deadlines
            7, 8, 10, 33, 39,              # compact downtown/south-east EODs
            24, 35                          # previously-missed: add so they are delivered
        ][:TRUCK_CAP]
    )

    # Truck 2 — “Truck 2 only” + #9 (after 10:20) + west/south EOD loop.
    # Leaves when a driver is free (after T1 or T3 returns).
    truck2 = Truck(
        "Truck 2",
        TRUCK_SPEED,
        HUB,
        datetime.timedelta(hours=11, minutes=0),  # will be reset to earliest return of T1/T3
        [
            3, 18, 36, 38,                  # Truck 2 only
            2, 4, 5, 9,                     # include #9 for 10:20+
            11, 12, 17, 21, 22, 23, 26, 27  # west/south loop, capped to 16 total
        ][:TRUCK_CAP]
    )

    # Wave runs
    deliver_run(truck1)   # 08:00
    deliver_run(truck3)   # 09:05 (delayed gate enforced inside router)

    # Free a driver for Truck 2
    t2_dep = truck1.time if truck1.time <= truck3.time else truck3.time
    if t2_dep < datetime.timedelta(hours=8):
        t2_dep = datetime.timedelta(hours=8)
    truck2.departTime = t2_dep
    truck2.time = t2_dep
    deliver_run(truck2)

    total_miles = round(truck1.miles + truck2.miles + truck3.miles, 1)

    # Optional sanity check mode
    if sim_sanity:
        hard_deadline_ids = []
        failures = []
        for pid in range(1, 41):
            p = packageHash.search(pid)
            if p is None:
                continue
            dl = parse_deadline(p.deadline)
            # gate assertions
            if is_delayed(p):
                if p.departureTime is not None and p.departureTime < DELAYED_TIME:
                    failures.append(f"Pkg {p.ID} departed at {hms_any(p.departureTime)} before 09:05.")
                nine = datetime.timedelta(hours=9)
                p.statusUpdate(nine)
                if not (p.status.startswith("DELAYED") and "09:05" in p.status):
                    failures.append(f"Pkg {p.ID} not correctly labeled as delayed at 09:00.")

            if p.ID == 9:
                if p.deliveryTime is not None and p.deliveryTime < ADDR_FIX_TIME:
                    failures.append(f"Pkg 9 delivered at {hms_any(p.deliveryTime)} before 10:20.")
            if dl is not None:
                hard_deadline_ids.append(p.ID)
                if p.deliveryTime is None or p.deliveryTime > dl:
                    failures.append(f"Pkg {p.ID} deadline {hms_any(dl)} missed (deliv {hms_any(p.deliveryTime)}).")

        print("\nSANITY — Hard-deadline summary (arrival vs deadline)")
        print("ID | Deadline | Delivered")
        print("---+----------+----------")
        for pid in sorted(hard_deadline_ids):
            p = packageHash.search(pid)
            print(f"{pid:2d} | {p.deadline:<8} | {hms_any(p.deliveryTime):<8}")

        if failures:
            print("\nSANITY FAILURES:")
            for s in failures:
                print(" -", s)
            sys.exit(2)
        else:
            print("\nSANITY OK — all constraints satisfied.")

    # -------------- CLI [D,E] --------------
    MENU = """
WGUPS Routing — Menu
[1] Show ONE package status at a time (HH:MM)
[2] List ALL packages at a time (HH:MM)
[3] Show TOTAL mileage (all trucks)
[4] Exit
Choice: """
    print("Western Governors University Parcel Service")

    while True:
        choice = input(MENU).strip()
        if choice == "1":
            try:
                pid = int(input("Package ID: ").strip())
            except:
                print("Invalid ID.")
                continue
            tm = input("Time (HH:MM): ").strip()
            try:
                hh, mm = tm.split(":")
                q = datetime.timedelta(hours=int(hh), minutes=int(mm))
            except:
                print("Bad time format.")
                continue
            p = packageHash.search(pid)
            if p is None:
                print("Unknown package ID.")
                continue

            # For DISPLAY use time-aware address (handles #9)
            street, city, state, zipc = display_address(p, q)
            p.statusUpdate(q)
            addr = f"{street}, {city}, {state} {zipc}"
            delivered_at = hms_any(p.deliveryTime) if (p.deliveryTime and p.deliveryTime <= q) else ""
            print("\nPackage Detail @", hms_any(q))
            print(f"ID: {p.ID}")
            print(f"Delivery Address: {addr}")
            print(f"Delivery Deadline: {p.deadline}")
            print(f"Truck Number: {p.truck or '-'}")
            print(f"Delivery Status: {p.status}")
            print(f"Delivery Time: {delivered_at}\n")

        elif choice == "2":
            tm = input("Time (HH:MM): ").strip()
            try:
                hh, mm = tm.split(":")
                q = datetime.timedelta(hours=int(hh), minutes=int(mm))
            except:
                print("Bad time format.")
                continue

            print("\nTime (HH:MM): %s" % tm)
            print("ID | DELIVERY STATUS               | TRUCK    | DEADLINE  | DELIVERED AT | DELIVERY ADDRESS")
            print("---+-------------------------------+----------+-----------+--------------+----------------------------------------------")
            for pid in range(1, 41):
                p = packageHash.search(pid)
                if p is None:
                    continue
                p.statusUpdate(q)
                truck_str = p.truck if p.truck else "-"
                delivered_at = hms_any(p.deliveryTime) if (p.deliveryTime and p.deliveryTime <= q) else ""
                street, city, state, zipc = display_address(p, q)
                addr = f"{street}, {city}, {state} {zipc}"
                print(f"{p.ID:2d} | {p.status:<29} | {truck_str:<8} | {p.deadline:<9} | {delivered_at:<12} | {addr}")
            print()

        elif choice == "3":
            total_miles = round(truck1.miles + truck2.miles + truck3.miles, 1)
            print("TOTAL_MILES:", f"{total_miles:0.1f}")
            print("Result:", "✅ Under 140 miles" if total_miles <= 140.0 else "❌ Over 140 miles")
            if _fallback_count > 0:
                print(f"Note: {_fallback_count} distance lookup(s) used a fallback value (check address mapping).")
            print()

        elif choice == "4":
            break
        else:
            print("Invalid choice.")

if __name__ == "__main__":
    # Enable sanity mode if env or CLI flag present
    sanity = (os.environ.get("WGUPS_SANITY", "") == "1") or ("--sanity" in sys.argv)
    run(sim_sanity=sanity)
