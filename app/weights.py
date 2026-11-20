def rebalance(rows):
    """Mutate draft bindings; distribute integer remainder in display order."""
    active = [r for r in rows if not r["archived"]]
    for row in rows:
        if row["archived"]:
            row["share"] = 0
            row["pinned"] = False
    pinned = [r for r in active if r["pinned"]]
    free = [r for r in active if not r["pinned"]]
    total = sum(r["share"] for r in pinned)
    if total > 100:
        raise ValueError("Сумма закреплённых весов превышает 100%.")
    if active and not free and total != 100:
        raise ValueError("Все веса закреплены: их сумма должна быть 100%. Снимите одну фиксацию.")
    if free:
        base, remainder = divmod(100 - total, len(free))
        for index, row in enumerate(free):
            row["share"] = base + (index < remainder)
    return rows


def signature(offers):
    """Compare binding semantics, ignoring API timestamps and binding IDs."""
    return sorted((int(o["offer_id"]), int(o["share"]), o.get("state", "active")) for o in offers)
