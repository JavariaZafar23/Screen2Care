from app.auth import create_user

DEV_PASSWORD = "Dev@12345"  # development only: change before any real use

# One login per seeded test patient: patient1 -> BC-HP-001 ... patient15 -> BC-HR-015
SEED_PATIENT_IDS = [
    "BC-HP-001", "BC-HP-002", "BC-HP-003", "BC-HP-004", "BC-HP-005",
    "BC-DL-006", "BC-DL-007", "BC-DL-008", "BC-DL-009", "BC-DL-010",
    "BC-HR-011", "BC-HR-012", "BC-HR-013", "BC-HR-014", "BC-HR-015",
]
PATIENT_USERS = [(f"patient{i}", pid) for i, pid in enumerate(SEED_PATIENT_IDS, start=1)]

# (username, role, staff_id, patient_id)
DEV_USERS = [
    ("admin1",     "Admin",       1,    None),
    ("radiology1", "Radiologist", 2,    None),
    ("clinician1", "Clinician",   3,    None),   # assigned: BC-HP-*, BC-HR-*
    ("clinician2", "Clinician",   11,   None),   # assigned: BC-DL-* (staff 11 comes from db/06_access.sql)
    ("pathology1", "Pathologist", 4,    None),
    ("navigator1", "Navigator",   5,    None),
    ("cmo1",       "CMO",         9,    None),
    ("scheduling1",  "SchedulingManager", 6, None),   # S2 breaches
    ("labdirector1", "LabDirector",       7, None),   # S3 breaches
    ("tumorboard1",  "TumorBoard",        8, None),   # S4 breaches
] + [(username, "Patient", None, pid) for username, pid in PATIENT_USERS]

if __name__ == "__main__":
    for username, role, staff_id, patient_id in DEV_USERS:
        create_user(username, DEV_PASSWORD, role, staff_id, patient_id)
        print(f"created {username} ({role}{', ' + patient_id if patient_id else ''})")
    print("Done. Dev password is", DEV_PASSWORD)
