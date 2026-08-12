from PIL import Image, ImageDraw, ImageFont

OUT = r"C:\Users\USER\Desktop\FYPilotProject\FYPilot-Complete-ER-Diagram.png"
W, H = 4800, 7600
BG = "#f8fafc"
TEXT = "#0f172a"
MUTED = "#475569"
LINE = "#64748b"

try:
    TITLE = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 58)
    GROUP = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 34)
    NAME = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 25)
    FIELD = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 19)
    SMALL = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 17)
except OSError:
    TITLE = GROUP = NAME = FIELD = SMALL = ImageFont.load_default()

groups = [
    ("Accounts & administration", "#dbeafe", [
        ("USER", ["PK Id", "UK Email", "Role", "IsMainAdmin", "FK LastActiveProjectId"]),
        ("STUDENT_PROFILE", ["PK Id", "FK UserId", "Major"]),
        ("SUPERVISOR_PROFILE", ["PK Id", "FK UserId", "Department"]),
        ("COMPANY_PROFILE", ["PK Id", "FK UserId", "CompanyName"]),
        ("EMAIL_VERIFICATION_CODE", ["PK Id", "FK UserId", "CodeHash"]),
        ("PASSWORD_RESET_TOKEN", ["PK Id", "FK UserId", "TokenHash"]),
        ("STUDENT_SKILL", ["PK Id", "FK UserId", "SkillName"]),
        ("NOTIFICATION", ["PK Id", "FK RecipientUserId", "FK ProjectId", "FK ActorUserId"]),
        ("IDEA_GENERATION_GUIDANCE", ["PK Id", "FK CreatedByUserId", "GuidanceType"]),
        ("HISTORICAL_FYP_PROJECT", ["PK Id", "FK CreatedByUserId", "Title"]),
        ("HISTORICAL_FYP_FUTURE_OPPORTUNITY", ["PK Id", "FK HistoricalFypProjectId", "FK CreatedByUserId"]),
        ("ACTIVITY", ["PK Id", "FK UserId", "ActivityType"]),
    ]),
    ("Projects & collaboration", "#dcfce7", [
        ("PROJECT", ["PK Id", "FK StudentId", "FK SupervisorId", "FK ProjectIdeaId", "FK DeletedByUserId"]),
        ("PROJECT_MEMBER", ["PK Id", "FK ProjectId", "FK UserId", "Role"]),
        ("PROJECT_INVITATION", ["PK Id", "FK ProjectId", "FK InvitedByUserId", "FK InvitedUserId", "FK TeammateRequestId"]),
        ("TEAMMATE_REQUEST", ["PK Id", "FK ProjectId", "FK RequestedByUserId", "FK MatchedUserId", "FK MatchedBySupervisorId"]),
        ("PROJECT_ACTIVITY", ["PK Id", "FK ProjectId", "FK UserId", "FK PreviousIdeaId", "FK NewIdeaId"]),
        ("PROJECT_DISCUSSION_MESSAGE", ["PK Id", "FK ProjectId", "FK UserId", "FK ReplyToMessageId"]),
        ("PROJECT_DISCUSSION_ATTACHMENT", ["PK Id", "FK MessageId", "FileName"]),
        ("PROJECT_TASK", ["PK Id", "FK ProjectId", "Status"]),
        ("MILESTONE", ["PK Id", "FK ProjectId", "Status"]),
        ("FEEDBACK", ["PK Id", "FK ProjectId", "FK SupervisorId"]),
        ("PROJECT_DOCUMENTATION", ["PK Id", "REF UserId", "REF ProjectIdeaId", "SupervisorStatus"]),
    ]),
    ("Ideas, market & AI", "#fef3c7", [
        ("PROJECT_IDEA", ["PK Id", "FK UserId", "FK GeneratedForProjectId", "Title", "IsSelected"]),
        ("FEASIBILITY_REPORT", ["PK Id", "FK IdeaId", "REF UserId"]),
        ("MARKET_DEMAND_ANALYSIS", ["PK Id", "FK ProjectIdeaId", "REF UserId"]),
        ("MARKET_DEMAND_SOURCE", ["PK Id", "FK MarketDemandAnalysisId", "Url"]),
        ("MARKET_SIMILAR_SOLUTION", ["PK Id", "FK MarketDemandAnalysisId", "Name"]),
        ("MARKET_OPPORTUNITY_SNAPSHOT", ["PK Id", "FK ProjectIdeaId", "FK UserId"]),
        ("MARKET_OPPORTUNITY_REGION", ["PK Id", "FK SnapshotId", "RegionKey"]),
        ("PROJECT_DNA_ANALYSIS", ["PK Id", "FK ProjectId", "FK ProjectIdeaId", "FK GeneratedByUserId"]),
        ("MENTOR_CHAT_SESSION", ["PK Id", "REF UserId", "REF IdeaId"]),
        ("CHAT_MESSAGE", ["PK Id", "REF UserId", "REF IdeaId", "FK MentorChatSessionId"]),
        ("AI_AGENT_JOB", ["PK Id", "UK JobId", "FK UserId", "FK ProjectId"]),
        ("AI_OUTPUT_REVIEW", ["PK Id", "REF JobId", "REF UserId", "REF ProjectIdeaId", "REF MentorChatSessionId"]),
        ("MARKET_NEED", ["PK Id", "Sector", "Title"]),
        ("PREVIOUS_PROJECT", ["PK Id", "Domain", "Title"]),
    ]),
    ("Planning & supervision", "#f3e8ff", [
        ("PROJECT_ROADMAP", ["PK Id", "FK IdeaId", "REF UserId"]),
        ("ROADMAP_PHASE", ["PK Id", "FK RoadmapId", "Status"]),
        ("SUPERVISOR_EVALUATION", ["PK Id", "FK ProjectId", "FK IdeaId", "FK SupervisorId"]),
        ("FEEDBACK_MESSAGE", ["PK Id", "REF EvaluationId", "REF SenderUserId", "REF ReplyToMessageId"]),
        ("SUPERVISOR_PREFERENCE_BATCH", ["PK Id", "FK StudentId", "Status"]),
        ("SUPERVISOR_PREFERENCE", ["PK Id", "FK BatchId", "FK StudentId", "FK SupervisorId"]),
        ("SUPERVISOR_ASSIGNMENT", ["PK Id", "FK ProjectId", "FK StudentId", "FK SupervisorId", "FK AssignedByAdminId"]),
        ("MEETING", ["PK Id", "FK ProjectId", "FK SupervisorId", "FK StudentId"]),
        ("GOOGLE_CALENDAR_TOKEN", ["PK Id", "FK SupervisorId", "RefreshToken"]),
        ("CHALLENGE", ["PK Id", "FK CompanyId", "Title"]),
    ]),
]

relationships = [
    ("USER", "STUDENT_PROFILE", "1", "0..1"), ("USER", "SUPERVISOR_PROFILE", "1", "0..1"),
    ("USER", "COMPANY_PROFILE", "1", "0..1"), ("USER", "EMAIL_VERIFICATION_CODE", "1", "0..*"),
    ("USER", "PASSWORD_RESET_TOKEN", "1", "0..*"), ("USER", "STUDENT_SKILL", "1", "0..*"),
    ("USER", "ACTIVITY", "1", "0..*"), ("USER", "PROJECT", "1", "0..*"),
    ("USER", "PROJECT_MEMBER", "1", "0..*"), ("PROJECT", "PROJECT_MEMBER", "1", "0..*"),
    ("PROJECT", "PROJECT_INVITATION", "1", "0..*"), ("USER", "PROJECT_INVITATION", "1", "0..*"),
    ("TEAMMATE_REQUEST", "PROJECT_INVITATION", "0..1", "0..*"), ("PROJECT", "TEAMMATE_REQUEST", "1", "0..*"),
    ("USER", "TEAMMATE_REQUEST", "1", "0..*"), ("PROJECT", "PROJECT_ACTIVITY", "1", "0..*"),
    ("USER", "PROJECT_ACTIVITY", "0..1", "0..*"), ("PROJECT_IDEA", "PROJECT_ACTIVITY", "0..1", "0..*"),
    ("PROJECT", "PROJECT_DISCUSSION_MESSAGE", "1", "0..*"), ("USER", "PROJECT_DISCUSSION_MESSAGE", "1", "0..*"),
    ("PROJECT_DISCUSSION_MESSAGE", "PROJECT_DISCUSSION_ATTACHMENT", "1", "0..*"),
    ("PROJECT", "PROJECT_TASK", "1", "0..*"), ("PROJECT", "MILESTONE", "1", "0..*"),
    ("PROJECT", "FEEDBACK", "1", "0..*"), ("USER", "FEEDBACK", "1", "0..*"),
    ("USER", "PROJECT_IDEA", "1", "0..*"), ("PROJECT", "PROJECT_IDEA", "0..1", "0..*"),
    ("PROJECT_IDEA", "PROJECT", "0..1", "0..1"), ("PROJECT_IDEA", "FEASIBILITY_REPORT", "1", "0..1"),
    ("PROJECT_IDEA", "MARKET_DEMAND_ANALYSIS", "1", "0..*"),
    ("MARKET_DEMAND_ANALYSIS", "MARKET_DEMAND_SOURCE", "1", "0..*"),
    ("MARKET_DEMAND_ANALYSIS", "MARKET_SIMILAR_SOLUTION", "1", "0..*"),
    ("PROJECT_IDEA", "MARKET_OPPORTUNITY_SNAPSHOT", "1", "0..*"),
    ("USER", "MARKET_OPPORTUNITY_SNAPSHOT", "1", "0..*"),
    ("MARKET_OPPORTUNITY_SNAPSHOT", "MARKET_OPPORTUNITY_REGION", "1", "0..*"),
    ("PROJECT", "PROJECT_DNA_ANALYSIS", "1", "0..*"), ("PROJECT_IDEA", "PROJECT_DNA_ANALYSIS", "1", "0..*"),
    ("USER", "PROJECT_DNA_ANALYSIS", "1", "0..*"), ("PROJECT_IDEA", "PROJECT_ROADMAP", "1", "0..*"),
    ("PROJECT_ROADMAP", "ROADMAP_PHASE", "1", "0..*"), ("MENTOR_CHAT_SESSION", "CHAT_MESSAGE", "1", "0..*"),
    ("USER", "AI_AGENT_JOB", "1", "0..*"), ("PROJECT", "AI_AGENT_JOB", "0..1", "0..*"),
    ("PROJECT", "SUPERVISOR_EVALUATION", "0..1", "0..*"),
    ("PROJECT_IDEA", "SUPERVISOR_EVALUATION", "1", "0..*"), ("USER", "SUPERVISOR_EVALUATION", "1", "0..*"),
    ("USER", "SUPERVISOR_PREFERENCE_BATCH", "1", "0..*"),
    ("SUPERVISOR_PREFERENCE_BATCH", "SUPERVISOR_PREFERENCE", "1", "0..*"),
    ("USER", "SUPERVISOR_PREFERENCE", "1", "0..*"), ("PROJECT", "SUPERVISOR_ASSIGNMENT", "0..1", "0..*"),
    ("USER", "SUPERVISOR_ASSIGNMENT", "1", "0..*"), ("PROJECT", "MEETING", "0..1", "0..*"),
    ("USER", "MEETING", "1", "0..*"), ("USER", "GOOGLE_CALENDAR_TOKEN", "1", "0..*"),
    ("USER", "NOTIFICATION", "1", "0..*"), ("PROJECT", "NOTIFICATION", "0..1", "0..*"),
    ("USER", "IDEA_GENERATION_GUIDANCE", "0..1", "0..*"),
    ("USER", "HISTORICAL_FYP_PROJECT", "0..1", "0..*"),
    ("HISTORICAL_FYP_PROJECT", "HISTORICAL_FYP_FUTURE_OPPORTUNITY", "1", "0..*"),
    ("USER", "HISTORICAL_FYP_FUTURE_OPPORTUNITY", "0..1", "0..*"), ("USER", "CHALLENGE", "1", "0..*"),
]

img = Image.new("RGB", (W, H), BG)
draw = ImageDraw.Draw(img)
draw.text((W // 2, 55), "FYPilot — Complete Entity Relationship Diagram", font=TITLE, fill=TEXT, anchor="ma")
draw.text((W // 2, 130), "PK = primary key   •   FK = enforced/convention relationship   •   REF = logical reference", font=FIELD, fill=MUTED, anchor="ma")

margin, gap, top = 90, 70, 220
col_w = (W - margin * 2 - gap * 3) // 4
box_h, row_gap = 225, 28
positions = {}

for col, (group_name, color, entities) in enumerate(groups):
    x = margin + col * (col_w + gap)
    draw.rounded_rectangle((x, top, x + col_w, top + 58), radius=16, fill=color, outline=TEXT, width=2)
    draw.text((x + col_w / 2, top + 29), group_name, font=GROUP, fill=TEXT, anchor="mm")
    y = top + 85
    for name, fields in entities:
        actual_h = max(box_h, 88 + len(fields) * 25)
        positions[name] = (x, y, x + col_w, y + actual_h)
        y += actual_h + row_gap

# Relationships are drawn first so entity boxes remain readable.
for idx, (a, b, ca, cb) in enumerate(relationships):
    if a not in positions or b not in positions:
        continue
    ax1, ay1, ax2, ay2 = positions[a]
    bx1, by1, bx2, by2 = positions[b]
    if ax1 == bx1:
        sx, sy = ax2 - 16, ay2
        ex, ey = bx2 - 16, by1
        if sy > ey:
            sx, sy, ex, ey = ax1 + 16, ay1, bx1 + 16, by2
        mid = sx + 18 + (idx % 4) * 8
        points = [(sx, sy), (mid, sy + 12), (mid, ey - 12), (ex, ey)]
    elif ax1 < bx1:
        sx, sy = ax2, (ay1 + ay2) // 2
        ex, ey = bx1, (by1 + by2) // 2
        mid = (sx + ex) // 2 + ((idx % 5) - 2) * 12
        points = [(sx, sy), (mid, sy), (mid, ey), (ex, ey)]
    else:
        sx, sy = ax1, (ay1 + ay2) // 2
        ex, ey = bx2, (by1 + by2) // 2
        mid = (sx + ex) // 2 + ((idx % 5) - 2) * 12
        points = [(sx, sy), (mid, sy), (mid, ey), (ex, ey)]
    draw.line(points, fill=LINE, width=3, joint="curve")
    draw.ellipse((sx - 5, sy - 5, sx + 5, sy + 5), fill=LINE)
    draw.polygon([(ex, ey), (ex - 13 if ex > sx else ex + 13, ey - 7), (ex - 13 if ex > sx else ex + 13, ey + 7)], fill=LINE)
    draw.text((sx + (10 if ex >= sx else -10), sy - 13), ca, font=SMALL, fill=MUTED, anchor="ls" if ex >= sx else "rs")
    draw.text((ex + (-10 if ex >= sx else 10), ey - 13), cb, font=SMALL, fill=MUTED, anchor="rs" if ex >= sx else "ls")

for group_name, color, entities in groups:
    for name, fields in entities:
        x1, y1, x2, y2 = positions[name]
        draw.rounded_rectangle((x1, y1, x2, y2), radius=14, fill="#ffffff", outline=TEXT, width=3)
        draw.rounded_rectangle((x1, y1, x2, y1 + 52), radius=14, fill=color, outline=TEXT, width=3)
        draw.rectangle((x1 + 2, y1 + 38, x2 - 2, y1 + 52), fill=color)
        draw.text((x1 + 18, y1 + 27), name, font=NAME, fill=TEXT, anchor="lm")
        fy = y1 + 70
        for field in fields:
            key = field.split()[0]
            color_field = "#1d4ed8" if key in {"PK", "UK"} else "#15803d" if key == "FK" else "#7c3aed" if key == "REF" else TEXT
            draw.text((x1 + 22, fy), field, font=FIELD, fill=color_field, anchor="la")
            fy += 25

content_bottom = max(box[3] for box in positions.values()) + 100
img = img.crop((0, 0, W, min(H, content_bottom)))
img.save(OUT, format="PNG", optimize=True)
print(OUT)
