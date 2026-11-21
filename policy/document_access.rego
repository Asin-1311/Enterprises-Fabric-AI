# Document access policy (CLAUDE.md sections 5 and 12; M13).
#
# Evaluated by OPA for every candidate chunk after retrieval and before re-ranking, in one
# batch per search (POST /v1/data/fabric/document_access/decisions). The same rule is the
# pre-filter inside Qdrant and OpenSearch (app/retrieval/acl.py) and the lite policy in
# code (app/adapters/lite/policy.py); tests check that all of them agree.
#
# 1. Role level: staff < manager < partner; a user sees documents at or below their level.
# 2. Engagement: a document of an engagement is visible only to that engagement's team
#    (members and its partner); partners see every engagement.
# Anything unknown (a role, an access level) is denied. Classification does not change
# who may see a document; it decides which model may read it (the private model for
# "restricted"), which the application enforces.
package fabric.document_access

import rego.v1

levels := {"staff": 0, "manager": 1, "partner": 2}

# One decision per document of input.documents, in order (true or false, never missing).
decisions := [ok | some doc in input.documents; ok := allowed(input.user, doc)]

# A single document: input.document.
default allow := false

allow if allowed(input.user, input.document)

allowed(user, doc) := true if {
	levels[doc.access_level] <= levels[user.role]
	engagement_ok(user, doc)
} else := false

engagement_ok(_, doc) if not "engagement_id" in object.keys(doc)

engagement_ok(_, doc) if doc.engagement_id == null

engagement_ok(_, doc) if doc.engagement_id == ""

engagement_ok(user, _) if user.role == "partner"

engagement_ok(user, doc) if doc.engagement_id in user.engagement_ids
