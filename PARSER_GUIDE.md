# Clara Health JSON Parser Guide

> Reference document for porting `parse_production_json` logic to any PDF generator.

---

## 1. API Response Structure

The raw API response from `POST /v1/reports/data/multiple` looks like this:

```json
{
  "success": true,
  "data": {
    "studentsData": [
      {
        "student": {
          "name": "...",
          "date_of_birth": "2010-05-12T00:00:00.000Z",
          "gender": "MALE",
          "class": "10",
          "section": "A",
          "roll_number": "22",
          "admission_number": "ADM001",
          "claraId": "CLARA-XXX",
          "school": {
            "schoolName": "ABC School",
            "claraId": "SCH-001"
          }
        },
        "campData": [
          {
            "parameter": { "name": "BIOMETRICS & VITALS" },
            "subParameter": {
              "name": "HEIGHT in CM",
              "analyticMap": { "value": "height" }
            },
            "value": "142",
            "comment": ""
          }
        ]
      }
    ]
  }
}
```

The `campData` array is the heart of all health data. Each entry has:

| Field | Description |
|---|---|
| `parameter.name` | Category (e.g. `BIOMETRICS & VITALS`, `DENTAL CHECKUP`) |
| `subParameter.name` | Display name — **can change, do not rely on it for mapping** |
| `subParameter.analyticMap.value` | Stable programmatic key — **use this for all field mapping** |
| `value` | The recorded health value (string, may be numeric or descriptive text) |
| `comment` | Doctor's comment for that field |

> **Important:** `analyticMap` can be `null` for some entries. Always guard:
> `(item.get('subParameter', {}).get('analyticMap') or {}).get('value', '')`

### How `pdf_service.py` feeds the parser

`pdf_service.py` extracts each student from `data.studentsData` and wraps it before calling the parser:

```python
students_data = api_response['data']['studentsData']   # list of student objects
for student_raw_data in students_data:
    wrapped = {"data": student_raw_data}               # wrap here
    parse_production_json(wrapped)
```

So `parse_production_json` always receives `{"data": {"student": {...}, "campData": [...]}}`.

---

## 2. Output Data Structure

After parsing, every page generator receives a single dict:

```
result = {
  camp_name           → school name (page 1 header)
  clara_id_camp       → school's Clara ID

  student: {
    name, dob,        → dob formatted as DD/MM/YYYY
    sex,              → 'M' or 'F' (first letter of gender, uppercased)
    class, section,
    roll_no, admission_no, clara_id
  }

  measurements: {
    height  → str (cm)
    weight  → str (kg)
    bmi     → str
  }

  vitals: {
    pulse_rate  → str (bpm)
    oxymetry    → str (%)
  }

  blood_work: {
    hemoglobin    → str (g/dl)
    anemia_status → 'Anemic' | 'Non-Anemic'
  }

  hygiene: {
    nail_hygiene      → 'Good' | 'Fair' | 'Poor'   (parsed from API)
    nail_observation  → str                         (comment from API)
    hair_hygiene      → 'Good' | 'Fair' | 'Poor'   (parsed from API)
    hair_observation  → str
    ear_hygiene       → 'Good' | 'Fair' | 'Poor'   (parsed from API)
    ear_observation   → str
  }

  medical_observations: {
    pallor             → { status: 'Present'|'Absent', comment: str }
    icterus            → { status, comment }
    cyanosis           → { status, comment }
    lymphadenopathy    → { status, comment }
    allergy            → { status, comment }
    skin               → { status, comment }
    clubbing           → { status, comment }
    bone_and_joints    → { status, comment }
    puberty_changes    → { status, comment }
  }

  ent: {
    hearing → str   ← hardcoded 'Normal' (not yet in API data)
    ear     → str
    throat  → str
    nose    → str
  }

  dental: {
    pit_fissure_caries          → { status: 'Present'|'Absent', comment }
    nursing_bottle_caries       → { status, comment }
    gum_inflammation            → { status, comment }
    bleeding                    → { status, comment }
    tartar                      → { status, comment }
    plaque                      → { status, comment }
    oral_hygiene                → { status: 'Good'|'Fair'|'Poor', comment }
    dentist_visit_recommendation→ { status: 'Yes'|'No', comment }
  }

  final_observations: {
    bmi_status        → 'Underweight'|'Normal'|'Overweight'|'Obese'  (auto-calculated)
    bmi_note          → str
    ent_status        → 'Normal'  (hardcoded default)
    ent_note          → str
    vitals_status     → 'Normal'|'Check Required'  (auto-calculated)
    vitals_note       → str
    hemoglobin_status → 'Normal'|'Low'
    hemoglobin_note   → str
    hygiene_note      → str
    medical_status    → 'Normal'|'Needs Attention'
    medical_note      → str  ('Doctor Visit Recommended' when triggered)
    dental_status     → 'Normal'|'Poor'
    dental_note       → str  ('Doctor Visit Recommended' when triggered)
  }
}
```

---

## 3. Parsing Rules by Section

### 3.1 School / Camp Name

School is **nested inside `student`**, not at the top `data` level.

```python
student_data = data.get('student', {})
school_data  = student_data.get('school', {})   # ← correct
# NOT: data.get('school', {})                   # ← wrong, always returns {}

camp_name      = school_data.get('schoolName', '')
clara_id_camp  = school_data.get('claraId', '')
```

---

### 3.2 BIOMETRICS & VITALS

Matched by `subParameter.name` (these names are stable for numeric fields).

| subParameter.name | Maps to |
|---|---|
| `HEIGHT in CM` | `measurements.height` |
| `WEIGHT in KG` | `measurements.weight` |
| `BMI` | `measurements.bmi` |
| `PULSE RATE in Bpm` | `vitals.pulse_rate` |
| `OXYMETRY in %` | `vitals.oxymetry` |
| `HEMOGLOBIN in g/dl` | `blood_work.hemoglobin` (see 3.2.1) |

#### 3.2.1 Hemoglobin special handling

The API sends a text sentence instead of a number. Handle both:

```python
try:
    hb = float(value)
    # threshold: hb < 11.0 → Low / Anemic
    # threshold: hb >= 11.0 → Normal / Non-Anemic
except ValueError:
    if 'below' in value.lower() or 'anemic' in value.lower():
        hb = 10.5   # placeholder → Low / Anemic
    else:
        hb = 12.5   # placeholder → Normal / Non-Anemic
```

---

### 3.3 PERSONAL HYGIENE

Matched by `analyticMap.value`. Status detected by keyword matching on `value`.

**HYGIENE_MAP** — note: all three keys have typos in the API

| analyticMap.value | Internal field | API typo note |
|---|---|---|
| `nail_hygeine` | `nail_hygiene` | 'ei' instead of 'ie' |
| `hair_hyiegne` | `hair_hygiene` | 'ie' transposed |
| `ear_hygiene` | `ear_hygiene` | correct |

**Status detection (Good / Fair / Poor):**

```python
v = value.lower()
if any(w in v for w in ['poor', 'unclean', 'overgrown', 'significant', 'immediate']):
    status = 'Poor'
elif any(w in v for w in ['fair', 'moderate', 'mild', 'buildup', 'needs']):
    status = 'Fair'
else:
    status = 'Good'   # default: covers 'clean', 'no dandruff', 'no wax', etc.
```

The `comment` field from the API is stored as the `_observation` value (e.g. `nail_observation`).

---

### 3.4 GENERAL EXAMINATION

Matched by `analyticMap.value`. Uses **absence-first detection** for status.

**MEDICAL_OBS_MAP**

| analyticMap.value | Internal field key | Example API value |
|---|---|---|
| `pallor` | `pallor` | `"Absent"` |
| `icterus` | `icterus` | `"Absent "` |
| `cyanosis` | `cyanosis` | `"Absent "` |
| `lympha_denopathy` | `lymphadenopathy` | `"Not Palpable "` |
| `allergy` | `allergy` | `"None Noted"` |
| `skin_assessment` | `skin` | `"Clear/Healthy "` |
| `clubbing` | `clubbing` | `"Absent "` |
| `bone_and_joint` | `bone_and_joints` | `"Normal Range "` |
| `puberty` | `puberty_changes` | `"puberty changes appropriate for age."` |

**Absence-first detection logic:**

```python
ABSENT_WORDS = [
    'absent', 'no ', 'none', 'not present', 'not palpable',
    'normal', 'healthy', 'clean', 'appropriate', 'clear'
]

is_absent = any(word in value.lower() for word in ABSENT_WORDS)
status = 'Absent' if is_absent else 'Present'
```

> Why absence-first? Presence-keyword detection fails for values like
> `"Visible signs of allergy (redness/rashes)"` — no presence word, but clearly Present.
> Defaulting to Present is safer.

> **Note on `lymphadenopathy`:** The API value is `"Not Palpable"` (not `"Absent"`).
> This is why `'not palpable'` must be in `ABSENT_WORDS` — without it, the field would
> wrongly resolve to `Present`.

**`doctor_visit` — handled separately (not in MEDICAL_OBS_MAP):**

| analyticMap.value | Trigger condition | Side effect |
|---|---|---|
| `doctor_visit` | `'recommended'` in value | Sets `medical_status = 'Needs Attention'` and `medical_note = 'Doctor Visit Recommended'` |

```python
elif analytic_key == 'doctor_visit':
    if 'recommended' in value.lower():
        final_observations['medical_status'] = 'Needs Attention'
        final_observations['medical_note']   = 'Doctor Visit Recommended'
```

---

### 3.5 DENTAL CHECKUP

Matched by `analyticMap.value`. Mixed status logic depending on field.

**DENTAL_MAP**

| analyticMap.value | Internal field key | Status logic |
|---|---|---|
| `dental_cavity` | `pit_fissure_caries` | Absence-first |
| `nursing_bottle_caries` | `nursing_bottle_caries` | Absence-first |
| `gum_health` | `gum_inflammation` | Absence-first |
| `other_condition` | `bleeding` | Absence-first |
| `dental_fluorosis` | `tartar` | Absence-first |
| `alignment` | `plaque` | Absence-first |
| `oral_hygiene` | `oral_hygiene` | Keyword: Good / Fair / Poor |
| `dental_visit` | `dentist_visit_recommendation` | `'Yes'` if `'visit a dentist'` in value |

**oral_hygiene keyword detection:**

```python
if 'good' in value.lower() or 'excellent' in value.lower():
    status = 'Good'
elif 'fair' in value.lower():
    status = 'Fair'
elif 'poor' in value.lower() or 'buildup' in value.lower():
    status = 'Poor'
else:
    status = value   # fallback: use raw value
```

**dental_visit side effect:**

When `dental_visit` resolves to `'Yes'`, also set:
```python
final_observations['dental_status'] = 'Poor'
final_observations['dental_note']   = 'Doctor Visit Recommended'
```

---

## 4. Auto-Calculated Final Observations

These are derived after the campData loop, not from the API directly.

### BMI Status
```python
bmi = float(measurements['bmi'])
if   bmi < 18.5:             → 'Underweight'
elif 18.5 <= bmi < 25:       → 'Normal'
elif 25   <= bmi < 30:       → 'Overweight'
else:                        → 'Obese'
```

### Vitals Status
```python
pulse = int(vitals['pulse_rate'])
oxy   = int(vitals['oxymetry'])
if 70 <= pulse <= 100 and oxy >= 95:  → 'Normal'
else:                                 → 'Check Required'
```

---

## 5. Hardcoded Defaults (Pending Data)

| Section | Fields | Default |
|---|---|---|
| ENT | `hearing`, `ear`, `throat`, `nose` | `'Normal'` |
| `ent_status` | final_observations | `'Normal'` |

Once ENT entries appear in API data, identify their `analyticMap.value` keys and wire up an `ENT_MAP` using the same absence-first pattern.

---

## 6. How to Call the Parser

The function expects **one student object** wrapped in a `data` key:

```python
result = parse_production_json({"data": student_raw_data})
# where student_raw_data = { "student": {...}, "campData": [...] }
```

Your dev/single-page generator must do the same wrap, or copy the function and adjust accordingly.
