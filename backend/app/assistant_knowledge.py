"""Allowlisted assistant copy; review scope and provenance in docs/ASSISTANT_PHASE3.md.

Only project entries describe checked-in MAWOS behavior. General entries are
educational copy, never evidence of MITE policy. No retrieval or user additions.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    category: str
    text: str
    short: str
    sources: tuple[str, ...] = ()
    official: bool = False


HELP = ("I can explain approved academic terms and help you use MAWOS. "
        "The authenticated backend capability response describes any record access available to your role. "
        "College policies require official documentation.")
KNOWLEDGE = {
    "greeting": Entry("conversation", "Hello! " + HELP, "Hello! Ask about your records, academic terms, or using MAWOS."),
    "thanks": Entry("conversation", "You're welcome! Let me know if you want help with another question.", "You're welcome!"),
    "identity": Entry("conversation", "I'm the MAWOS academic assistant. " + HELP, "I'm the MAWOS academic assistant. I explain academic terms and look up authorized records."),
    "help": Entry("conversation", HELP, "Ask one question about attendance, fees, internal marks, eligibility, or an academic term."),
    "attendance_meaning": Entry("institutional_faq", "General explanation: attendance shortage means attendance is below the applicable requirement. It does not by itself establish your exam eligibility. Ask about your attendance for a personal record; confirm official requirements with the academic office.", "General explanation: attendance shortage means being below the applicable attendance requirement. Confirm college rules with the academic office."),
    "attendance_requirement": Entry("institutional_faq", "General explanation: attendance requirements encourage regular participation. I do not have approved MITE documentation explaining why a 75% threshold is required or what exceptions apply. Ask the academic office or consult official regulations.", "I do not have approved MITE documentation for the 75% rule or its rationale. Please ask the academic office."),
    "cie": Entry("institutional_faq", "General explanation: CIE means Continuous Internal Evaluation: assessment during a course, such as internal tests. Assessment components and weightings vary; ask your faculty or consult the official course assessment document for your course.", "General explanation: CIE is Continuous Internal Evaluation, assessment during a course. Your faculty can confirm its components."),
    "eligibility_meaning": Entry("institutional_faq", "General explanation: hall-ticket eligibility indicates whether the applicable checks permit exam admission. This explanation is not a personal decision or an official MITE policy. Ask 'Am I eligible for a hall ticket?' for MAWOS's server-calculated result; confirm exam arrangements with the examination office.", "General explanation: hall-ticket eligibility indicates whether exam-admission checks are met. Ask about your eligibility for a personal result."),
    "fees_meaning": Entry("institutional_faq", "General explanation: outstanding fees are amounts still unpaid; fines are additional charges, for example for late payment. Which charges apply depends on official rules. Ask the accounts office about policy or ask about your fees for the recorded amounts.", "General explanation: outstanding fees are unpaid amounts; fines are additional charges. The accounts office can confirm the rules."),
    "improve_attendance": Entry("institutional_faq", "General study guidance: attend scheduled classes consistently, track your attendance, and speak with your faculty about missed work or incorrect entries. I cannot promise that future attendance will meet an eligibility rule or calculate an exception. Ask the academic office about approved procedures.", "General study guidance: attend consistently, track attendance, and discuss missed classes or incorrect entries with your faculty."),
    "mawos": Entry("institutional_faq", "MAWOS is this project's event-driven multi-agent workflow orchestration engine for universities. Its assistant provides read-only attendance, fees, internal marks, and hall-ticket eligibility answers within the signed-in user's authorized scope. This describes the project, not an official MITE policy.", "MAWOS is a university workflow orchestration project with an assistant for authorized read-only academic records.", ("README.md", "backend/app/agents/tools.py"), True),
    "reason_codes": Entry("institutional_faq", "In the checked-in MAWOS implementation, hall-ticket reasons report attendance below the configured threshold, outstanding fees pending, or attendance OK and fees cleared when both checks pass. An unknown student produces an unavailable-student reason. The backend calculates the decision; the assistant cannot change it. These are project implementation reasons, not verified MITE regulations.", "MAWOS hall-ticket reasons describe attendance and fee-clearance checks. The backend decides eligibility; these project checks are not verified MITE regulations.", ("backend/app/agents/eligibility.py",), True),
    "rag": Entry("conversation", "RAG means Retrieval-Augmented Generation. It combines information retrieved from an external knowledge source with a generative model's response.", "RAG means Retrieval-Augmented Generation: retrieved knowledge is supplied to a generative model.", ("backend/app/assistant_knowledge.py",), False),
}
