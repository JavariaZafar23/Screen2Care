# Closing a care loop without treatment (with a reason, never deleting it) and reopening it.
from app.care_loop import check_sla
from app.rules_runner import run_engine
from tests.test_sla import PID, alert_roles, birads5_patient, logged_in, make_overdue, sql, task  # noqa: F401

NOTE = "Three calls and a letter, no reply from the patient."


def close(client, reason="LostToFollowUp", note=NOTE):
    return client.post(f"/api/patients/{PID}/close-loop", json={"reason": reason, "note": note})


def no_answer(client, times, channel="Phone"):
    for _ in range(times):
        assert client.post(f"/api/patients/{PID}/contact",
                           json={"channel": channel, "outcome": "NoAnswer"}).status_code == 200


def episode():
    return sql("""SELECT * FROM care_episode WHERE patient_id = :p
                  ORDER BY opened_at DESC, episode_id DESC LIMIT 1""", p=PID).mappings().first()


def test_navigator_closes_and_the_patient_leaves_the_list(birads5_patient):
    run_engine(PID)
    nav = logged_in("navigator1")
    no_answer(nav, 1, channel="Letter")                        # a letter counts as an attempt
    make_overdue(1)
    check_sla()
    assert alert_roles() == {1: "Navigator"}
    assert PID in nav.get("/").text

    res = close(nav)                                           # no minimum number of attempts
    assert res.status_code == 200 and res.json()["after"] == f"/?closed={PID}&kind=care"
    page = nav.get(res.json()["after"]).text
    assert "is no longer in the care loop" in page and "removed from your list" in page
    assert f'href="/loop/{PID}"' not in page                    # gone from the navigator's dashboard
    assert nav.get(f"/loop/{PID}").status_code == 403
    cmo = logged_in("cmo1").get("/").text                      # the CMO keeps the record
    assert "Loops closed without treatment" in cmo and NOTE in cmo
    ep = episode()
    assert ep["status"] == "Closed-LostToFollowUp" and ep["closure_note"] == NOTE and ep["closed_by"] == 5
    assert task(1)["status"] == "Cancelled"                    # the clock stopped
    assert alert_roles() == {}                                 # the alert is resolved, not deleted
    assert sql("SELECT COUNT(*) FROM sla_alert WHERE patient_id = :p", p=PID).scalar() == 1
    assert sql("SELECT resolved_at FROM team_notification WHERE patient_id = :p", p=PID).scalar() is not None
    check_sla()                                                # a closed loop never breaches again
    assert alert_roles() == {}
    # The patient record is kept and the engine does not quietly reopen the same result
    assert sql("SELECT COUNT(*) FROM patient WHERE patient_id = :p", p=PID).scalar() == 1
    run_engine(PID)
    assert episode()["status"] == "Closed-LostToFollowUp"


def test_who_may_close_with_which_reason(birads5_patient):
    run_engine(PID)
    nav, doctor, cmo = logged_in("navigator1"), logged_in("clinician1"), logged_in("cmo1")
    assert close(nav, "Declined").status_code == 403           # only a doctor records a refusal
    assert close(doctor, "Transferred").status_code == 403
    assert close(logged_in("scheduling1"), "Deceased").status_code == 403
    assert close(cmo, "Transferred", note="short").status_code == 422     # a real note is required
    assert close(doctor, "Declined", note="Risk explained in clinic; patient refuses biopsy.").status_code == 200
    assert episode()["status"] == "Closed-Declined"
    assert close(cmo, "Deceased").status_code == 409           # nothing open any more


def test_cmo_reviews_and_reopens(birads5_patient):
    run_engine(PID)
    cmo, nav = logged_in("cmo1"), logged_in("navigator1")
    assert nav.post(f"/api/patients/{PID}/contact", json={"channel": "Phone", "outcome": "Reached"}).status_code == 200
    assert nav.post(f"/api/patients/{PID}/biopsy",
                    json={"scheduled_for": "2030-01-10T09:00:00Z"}).status_code == 200
    assert close(cmo, "Transferred", note="Moved to Shaukat Khanum, records sent.").status_code == 200
    assert sql("SELECT b.status FROM biopsy b JOIN care_episode e USING (episode_id) WHERE e.patient_id = :p",
               p=PID).scalar() == "Cancelled"                  # the booked slot is released
    assert task(2)["status"] == "Met"                           # finished clocks keep their result
    assert sql("""SELECT COUNT(*) FROM sla_task t JOIN care_episode e USING (episode_id)
                  WHERE e.patient_id = :p AND t.completed_at IS NULL AND t.status <> 'Cancelled'""",
               p=PID).scalar() == 0                             # nothing left running

    dash = cmo.get("/").text
    assert "Loops closed without treatment" in dash and "Moved to Shaukat Khanum" in dash
    assert "Loop closed" in cmo.get(f"/loop/{PID}").text

    assert nav.post(f"/api/patients/{PID}/reopen-loop", json={"note": "Patient is back."}).status_code == 403
    res = cmo.post(f"/api/patients/{PID}/reopen-loop", json={"note": "Patient came back and wants the biopsy."})
    assert res.status_code == 200
    new = episode()
    assert new["status"] == "Open" and new["reopened_from"] is not None
    assert task(1) is not None
    assert cmo.post(f"/api/patients/{PID}/reopen-loop", json={"note": "Patient came back again."}).status_code == 409
    assert "reopened since" in cmo.get("/").text


def test_deceased_cannot_be_reopened(birads5_patient):
    run_engine(PID)
    cmo = logged_in("cmo1")
    assert close(cmo, "Deceased", note="Death certificate received 08 Nov.").status_code == 200
    assert cmo.post(f"/api/patients/{PID}/reopen-loop", json={"note": "Trying to reopen it."}).status_code == 409
