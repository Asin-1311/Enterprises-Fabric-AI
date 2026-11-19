# Tests for document_access.rego:  opa test policy/ -v   (run in Docker by the tests)
package fabric.document_access_test

import rego.v1

import data.fabric.document_access

staff_a := {"user_id": "S1", "role": "staff", "engagement_ids": ["ENG-A"]}

manager_none := {"user_id": "M2", "role": "manager", "engagement_ids": []}

partner := {"user_id": "P1", "role": "partner", "engagement_ids": []}

firm_staff := {"doc_id": "POL-1", "access_level": "staff", "engagement_id": null}

firm_manager := {"doc_id": "POL-2", "access_level": "manager", "engagement_id": ""}

eng_a := {"doc_id": "ENG-A-1", "access_level": "staff", "engagement_id": "ENG-A"}

eng_b := {"doc_id": "ENG-B-1", "access_level": "staff", "engagement_id": "ENG-B"}

test_role_levels if {
	document_access.allow with input as {"user": staff_a, "document": firm_staff}
	not document_access.allow with input as {"user": staff_a, "document": firm_manager}
	document_access.allow with input as {"user": manager_none, "document": firm_manager}
	document_access.allow with input as {"user": partner, "document": firm_manager}
}

test_engagement_team_only if {
	document_access.allow with input as {"user": staff_a, "document": eng_a}
	not document_access.allow with input as {"user": staff_a, "document": eng_b}
	not document_access.allow with input as {"user": manager_none, "document": eng_a}
}

test_partners_see_every_engagement if {
	document_access.allow with input as {"user": partner, "document": eng_b}
}

test_unknown_values_are_denied if {
	not document_access.allow with input as {"user": {"role": "admin", "engagement_ids": []}, "document": firm_staff}
	not document_access.allow with input as {"user": partner, "document": {"access_level": "secret"}}
	not document_access.allow with input as {"user": staff_a}
}

test_missing_engagement_field_is_firm_wide if {
	document_access.allow with input as {"user": staff_a, "document": {"access_level": "staff"}}
}

test_batch_decisions_keep_order_and_say_false if {
	document_access.decisions == [true, false, true, false] with input as {
		"user": staff_a,
		"documents": [firm_staff, firm_manager, eng_a, eng_b],
	}
}

test_classification_does_not_change_access if {
	document_access.allow with input as {"user": staff_a, "document": object.union(firm_staff, {"classification": "restricted"})}
}
