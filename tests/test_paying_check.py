"""scripts/paying_check.py: the admin page's Paying number against Stripe's.

Owner, 2026-11-14: "It says I have only 5 paying on Stripe's site but 7 on my
admin page." The admin page counts ACCOUNTS the app has marked active; Stripe
counts CUSTOMERS it is charging. Nothing compared the two by name, so a
difference could only be stared at. This script lists them side by side, and
these tests hold each way the lists can differ.
"""

import importlib.util
import pathlib
import types

import pytest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "paying_check.py"
spec = importlib.util.spec_from_file_location("paying_check", _PATH)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def sub(status="active", last=25.0, **kw):
    return {"id": "sub_1", "status": status, "paused": False, "cancel_at_period_end": False,
            "last_total": last, **kw}


def acct(name, cust, plan="pro", monthly=25.0):
    return {"name": name, "customer": cust, "plan": plan, "monthly": monthly}


@pytest.mark.parametrize("customer, subs, code", [
    ("ok", [sub()], "PAYING"),
    ("ok", [sub(cancel_at_period_end=True)], "PAYING"),          # still charged until it ends
    ("ok", [sub(last=None)], "PAYING"),                           # no invoice read: not called free on a guess
    ("ok", [sub(last=0.0)], "FREE"),                              # a 100% off code
    ("ok", [sub(paused=True)], "PAUSED"),
    ("ok", [sub("trialing")], "TRIAL"),
    ("ok", [sub("canceled")], "NOT ACTIVE"),
    ("ok", [sub("past_due")], "NOT ACTIVE"),
    ("ok", [], "NOT ACTIVE"),
    ("deleted", [], "GONE"),
    ("missing", [], "GONE"),
    ("error", [], "UNKNOWN"),
])
def test_what_stripe_says_about_one_customer(customer, subs, code):
    assert P.verdict(customer, subs)[0] == code


def test_a_live_subscription_outranks_a_cancelled_duplicate():
    """A customer can hold a dead duplicate that is newer than the live one."""
    assert P.verdict("ok", [sub("canceled"), sub("active")])[0] == "PAYING"
    assert P.best_sub([sub("past_due"), sub("active"), sub("canceled")])["status"] == "active"


def _seven_against_five():
    """The owner's numbers, with the two ways an account can be on the admin
    page and not be charged: its customer is gone, and its subscription is
    free."""
    paying = [acct(f"user{i}", f"cus_live{i}") for i in range(1, 6)]
    paying += [acct("ghost", "cus_deleted1", plan="legacy", monthly=15.0), acct("comped", "cus_free01")]
    stripe = {"customers": {**{f"cus_live{i}": "ok" for i in range(1, 6)}, "cus_deleted1": "missing", "cus_free01": "ok"},
              "subs": {**{f"cus_live{i}": [sub()] for i in range(1, 6)}, "cus_free01": [sub(last=0.0)]}}
    return paying, stripe


def test_seven_on_the_admin_page_five_in_stripe_names_the_two():
    paying, stripe = _seven_against_five()
    r = P.compare(paying, [], stripe)
    assert (r["admin"], r["charged"]) == (7, 5)
    assert sorted(r["extra"]) == [("comped", "FREE"), ("ghost", "GONE")]
    assert r["uncounted"] == []
    text = "\n".join(P.report(r, "LIVE"))
    assert "THE ADMIN PAGE COUNTS 7 AS PAYING" in text
    assert "on the admin page, not charged in Stripe: ghost  (GONE)" in text
    assert "on the admin page, not charged in Stripe: comped  (FREE)" in text
    assert "(Stripe is charging 5)" in text


def test_the_sum_always_lands_on_what_stripe_is_charging():
    """admin - not charged + uncounted = charged, whatever the mix, or the
    last line of the report would be a number nobody can check."""
    paying, stripe = _seven_against_five()
    paying.append(acct("twin", "cus_live1"))                       # two accounts, one customer
    stripe["subs"]["cus_staff1"] = [sub()]                         # charged, not on the admin page
    others = [{"name": "boss", "customer": "cus_staff1", "why": "a staff account: never counted"}]
    stripe["subs"]["cus_nobody"] = [sub()]                         # charged, no account at all
    r = P.compare(paying, others, stripe)
    assert r["admin"] - len(r["extra"]) + len(r["uncounted"]) == r["charged"] == 7
    assert ("twin", "SHARED") in r["extra"]
    assert {u["name"]: u["why"] for u in r["uncounted"]} == {
        "boss": "a staff account: never counted", "": "no account is linked to this customer"}
    text = "\n".join(P.report(r, "LIVE"))
    assert "[same customer as user1]" in text and "[same customer as twin]" in text


def test_matching_lists_say_so_instead_of_inventing_a_difference():
    paying = [acct("a", "cus_aaaaaa1"), acct("b", "cus_bbbbbb1")]
    stripe = {"customers": {"cus_aaaaaa1": "ok", "cus_bbbbbb1": "ok"},
              "subs": {"cus_aaaaaa1": [sub()], "cus_bbbbbb1": [sub()]}}
    r = P.compare(paying, [], stripe)
    assert r["extra"] == [] and r["uncounted"] == [] and r["charged"] == 2
    assert "The two lists are the same people." in "\n".join(P.report(r, "LIVE"))


def test_the_report_never_prints_a_whole_stripe_id():
    paying, stripe = _seven_against_five()
    text = "\n".join(P.report(P.compare(paying, [], stripe), "LIVE"))
    for a in paying:
        assert a["customer"] not in text
    assert "…leted1" in text and P.tail("cus_AbCdEf123456") == "…123456"


# ── reading Stripe ───────────────────────────────────────────────────────────

class _Obj(types.SimpleNamespace):
    """A Stripe object: attributes, not a dict (stripe-python's are not dicts)."""


def _fake_client(pages, customers):
    import stripe
    calls = {"list": [], "retrieve": []}

    class Subs:
        def list(self, params):
            calls["list"].append(dict(params))
            return pages[len(calls["list"]) - 1]

    class Customers:
        def retrieve(self, cid):
            calls["retrieve"].append(cid)
            got = customers[cid]
            if got == "missing":
                raise stripe.InvalidRequestError("No such customer: '%s'" % cid, "customer")
            if got == "boom":
                raise ConnectionError("network")
            return _Obj(id=cid, deleted=(got == "deleted"))

    return _Obj(subscriptions=Subs(), customers=Customers()), calls


def test_it_reads_every_page_and_the_invoice_each_subscription_last_sent():
    s1 = _Obj(id="sub_1", status="active", customer="cus_a", pause_collection=None, cancel_at_period_end=False,
              latest_invoice=_Obj(id="in_1", total=2500))
    s2 = _Obj(id="sub_2", status="active", customer=_Obj(id="cus_b"), pause_collection=_Obj(behavior="void"),
              cancel_at_period_end=True, latest_invoice=_Obj(id="in_2", total=0))
    s3 = _Obj(id="sub_3", status="canceled", customer="cus_a", pause_collection=None, cancel_at_period_end=False,
              latest_invoice="in_3")                               # not expanded: no figure, not zero
    client, calls = _fake_client(
        [_Obj(data=[s1, s2], has_more=True), _Obj(data=[s3], has_more=False)],
        {"cus_gone": "missing", "cus_del": "deleted", "cus_net": "boom", "cus_quiet": "ok"})
    got = P.read_stripe(client, {"cus_a", "cus_gone", "cus_del", "cus_net", "cus_quiet"})
    assert calls["list"][0]["expand"] == ["data.latest_invoice", "data.discounts"] and calls["list"][0]["status"] == "all"
    assert calls["list"][1]["starting_after"] == "sub_2"
    assert [s["last_total"] for s in got["subs"]["cus_a"]] == [25.0, None]
    assert got["subs"]["cus_b"] == [{"id": "sub_2", "status": "active", "paused": True,
                                     "cancel_at_period_end": True, "last_total": 0.0, "discount": "none"}]
    assert got["customers"] == {"cus_a": "ok", "cus_gone": "missing", "cus_del": "deleted",
                                "cus_net": "error", "cus_quiet": "ok"}
    assert "cus_a" not in calls["retrieve"], "a customer holding subscriptions exists: no second question"


def test_it_counts_the_accounts_the_admin_page_counts(monkeypatch):
    """The same test as the Paying tile (api._counts_as_paying), not a copy."""
    from src.auth import users as user_store
    users = [
        {"id": "1", "username": "payer", "subscription_status": "active", "stripe_customer_id": "cus_1", "plan": "pro", "monthly_price": 12.5},
        {"id": "2", "twitch_login": "gifted", "subscription_status": "active", "stripe_customer_id": "cus_2", "plan_source": "granted"},
        {"id": "3", "username": "boss", "subscription_status": "active", "stripe_customer_id": "cus_3", "is_admin": True},
        {"id": "4", "username": "lapsed", "subscription_status": "inactive", "stripe_customer_id": "cus_4"},
        {"id": "5", "username": "free", "subscription_status": "active"},          # no customer: not Stripe's business
    ]
    monkeypatch.setattr(user_store, "get_all", lambda: users)
    paying, others = P.read_accounts()
    assert paying == [{"name": "payer", "customer": "cus_1", "plan": "pro", "monthly": 12.5}]
    assert {o["name"]: o["why"] for o in others} == {
        "gifted": "its plan was granted by an admin, so it is counted as comped",
        "boss": "a staff account: never counted",
        "lapsed": "the app has it as 'inactive', not active"}


def test_it_only_reads():
    """An audit that fixes things is not an audit (CLAUDE.md)."""
    src = _PATH.read_text()
    for call in (".update(", ".modify(", ".cancel(", ".delete(", ".create(", "update_subscription", "set_plan(", "set_billing("):
        assert call not in src, call


# ── the price check ─────────────────────────────────────────────────────────
# Production, 2026-11-14: four of the five paying accounts had a last invoice
# of $6.25 and the admin page counted each at $25.00 a month. Whether that is
# right depends on one thing: is the discount still on the subscription.

@pytest.mark.parametrize("sub_fields, want", [
    ({}, "none"),
    ({"discounts": []}, "none"),
    ({"discounts": ["di_123"]}, "unknown"),                               # an id: attached, end not given
    ({"discounts": [_Obj(id="di_1", end=None)]}, "forever"),
    ({"discounts": [_Obj(id="di_1", end=1800000000)]}, 1800000000.0),
    ({"discount": _Obj(id="di_old", end=1790000000), "discounts": []}, 1790000000.0),   # the older API's single field
    ({"discounts": [_Obj(id="a", end=1790000000), _Obj(id="b", end=1800000000)]}, 1800000000.0),
])
def test_whether_a_discount_is_still_on_the_subscription(sub_fields, want):
    assert P._discount_state(_Obj(id="sub_1", **sub_fields)) == want


def _priced(discount, last=6.25, monthly=25.0):
    paying = [acct("user", "cus_user01", monthly=monthly)]
    stripe = {"customers": {"cus_user01": "ok"}, "subs": {"cus_user01": [sub(last=last, discount=discount)]}}
    return P.compare(paying, [], stripe)


def test_a_first_month_code_that_is_used_up_leaves_the_admin_figure_right():
    r = _priced("none")
    assert r["prices"] == [{"name": "user", "app": 25.0, "last": 6.25, "discount": "none", "still_discounted": False}]
    text = "\n".join(P.report(r, "LIVE"))
    assert "no discount on it now: a one-off, so $25.00 is right from the next invoice" in text
    assert "too high" not in text


@pytest.mark.parametrize("discount, words", [
    ("forever", "a discount that never ends"),
    (1800000000.0, "a discount until Jan 15, 2027"),
    ("unknown", "a discount (end not readable)"),
])
def test_a_discount_still_running_means_the_admin_figure_is_too_high(discount, words):
    text = "\n".join(P.report(_priced(discount), "LIVE"))
    assert words + ": the admin page is $18.75 a month too high for this account" in text
    assert "monthly revenue is about $18.75 too high" in text


def test_an_invoice_that_matches_is_not_listed():
    r = _priced("none", last=25.0)
    assert r["prices"] == []
    assert "every paying account's last invoice matches" in "\n".join(P.report(r, "LIVE"))


def test_a_refused_expansion_costs_the_discounts_not_the_comparison():
    """If this API version will not expand discounts, read without them."""
    import stripe
    s1 = _Obj(id="sub_1", status="active", customer="cus_a", pause_collection=None, cancel_at_period_end=False,
              latest_invoice=_Obj(id="in_1", total=625), discounts=["di_1"])
    asked = []

    class Subs:
        def list(self, params):
            asked.append(list(params["expand"]))
            if "data.discounts" in params["expand"]:
                raise stripe.InvalidRequestError("cannot expand", "expand")
            return _Obj(data=[s1], has_more=False)

    got = P.read_stripe(_Obj(subscriptions=Subs(), customers=None), {"cus_a"})
    assert asked == [["data.latest_invoice", "data.discounts"], ["data.latest_invoice"]]
    assert got["subs"]["cus_a"][0]["last_total"] == 6.25 and got["subs"]["cus_a"][0]["discount"] == "unknown"


def test_it_names_whose_card_is_failing_counted_or_not():
    """Before the fix they were on the admin's list; after it they are not.
    Either way these are the people a reminder can still bring back."""
    stripe = {"customers": {}, "subs": {"cus_ok00001": [sub()], "cus_pd00001": [sub("past_due")],
                                        "cus_pd00002": [sub("past_due")], "cus_dead001": [sub("canceled")]}}
    before = P.compare([acct("payer", "cus_ok00001"), acct("zed", "cus_pd00001"), acct("Abe", "cus_pd00002")],
                       [{"name": "gone", "customer": "cus_dead001", "why": "x"}], stripe)
    after = P.compare([acct("payer", "cus_ok00001")],
                      [{"name": "zed", "customer": "cus_pd00001", "why": "x"},
                       {"name": "Abe", "customer": "cus_pd00002", "why": "x"}], stripe)
    assert before["failing"] == after["failing"] == ["Abe", "zed"]
    assert (before["admin"], after["admin"], after["charged"]) == (3, 1, 1)
    text = "\n".join(P.report(after, "LIVE"))
    assert "CARD FAILING" in text and "  Abe, zed" in text
    assert "CARD FAILING" not in "\n".join(P.report(P.compare([acct("payer", "cus_ok00001")], [], stripe), "LIVE"))
