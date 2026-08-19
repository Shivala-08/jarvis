# ADHD Co-Processor — Dashboard

> Auto-generated queries. Do not edit by hand.
> Notes are written here by the braindump agent — you never file anything manually.

---

## Today's Captures

```dataview
TABLE timestamp, mood_hint, tags
FROM ""
WHERE contains(tags, "braindump")
AND file.cday = date(today)
SORT timestamp DESC
```

---

## This Week

```dataview
TABLE timestamp, mood_hint, tags
FROM ""
WHERE contains(tags, "braindump")
AND file.cday >= date(today) - dur(7 days)
SORT timestamp DESC
```

---

## By Tag

```dataview
TABLE length(rows) AS "Count"
FROM ""
WHERE contains(tags, "braindump")
FLATTEN tags AS tag
GROUP BY tag
SORT length(rows) DESC
```

---

## All Captures (recent first)

```dataview
TABLE timestamp, mood_hint
FROM ""
WHERE contains(tags, "braindump")
SORT file.ctime DESC
LIMIT 50
```

---

*Last refreshed: auto-updated by Dataview plugin on note open.*
