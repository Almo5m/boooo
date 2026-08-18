import os
import json
import datetime
from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

from database import (
    get_session, init_db, Subject, Question, Exam, ExamResult,
    ScheduledMessage, GroupSettings, Warning,
)

load_dotenv()

DASH_USER = os.getenv("DASHBOARD_USERNAME", "admin")
DASH_PASS = os.getenv("DASHBOARD_PASSWORD", "admin")
SECRET_KEY = os.getenv("SECRET_KEY", "insecure-dev-key")

app = FastAPI(title="لوحة تحكم بوت الصف الثالث الثانوي")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.filters["fromjson"] = lambda s: json.loads(s) if s else []

# مرجع للبوت والـ scheduler هيتحقن من main.py عشان نقدر نبعت رسايل من اللوحة
bot_instance = None


def set_bot_instance(bot):
    global bot_instance
    bot_instance = bot


def require_login(request: Request):
    if not request.session.get("logged_in"):
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return True


@app.on_event("startup")
async def startup():
    init_db()


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == DASH_USER and password == DASH_PASS:
        request.session["logged_in"] = True
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": "بيانات غلط"})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=302)
    session = get_session()
    try:
        stats = {
            "subjects": session.query(Subject).count(),
            "questions": session.query(Question).count(),
            "exams": session.query(Exam).count(),
            "results": session.query(ExamResult).count(),
            "scheduled": session.query(ScheduledMessage).filter_by(is_active=True).count(),
        }
        recent_results = session.query(ExamResult).order_by(ExamResult.finished_at.desc()).limit(10).all()
        return templates.TemplateResponse(
            "home.html", {"request": request, "stats": stats, "recent_results": recent_results}
        )
    finally:
        session.close()


# ---------------- المواد ----------------

@app.get("/subjects", response_class=HTMLResponse)
async def subjects_page(request: Request):
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=302)
    session = get_session()
    try:
        subjects = session.query(Subject).all()
        return templates.TemplateResponse("subjects.html", {"request": request, "subjects": subjects})
    finally:
        session.close()


@app.post("/subjects/add")
async def add_subject(request: Request, name: str = Form(...)):
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=302)
    session = get_session()
    try:
        if not session.query(Subject).filter_by(name=name).first():
            session.add(Subject(name=name))
            session.commit()
        return RedirectResponse("/subjects", status_code=302)
    finally:
        session.close()


@app.get("/subjects/delete/{subject_id}")
async def delete_subject(request: Request, subject_id: int):
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=302)
    session = get_session()
    try:
        s = session.query(Subject).filter_by(id=subject_id).first()
        if s:
            session.delete(s)
            session.commit()
        return RedirectResponse("/subjects", status_code=302)
    finally:
        session.close()


# ---------------- الأسئلة ----------------

@app.get("/questions", response_class=HTMLResponse)
async def questions_page(request: Request):
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=302)
    session = get_session()
    try:
        questions = session.query(Question).order_by(Question.id.desc()).all()
        subjects = session.query(Subject).all()
        for q in questions:
            q.options_list = json.loads(q.options)
        return templates.TemplateResponse(
            "questions.html", {"request": request, "questions": questions, "subjects": subjects}
        )
    finally:
        session.close()


@app.post("/questions/add")
async def add_question(
    request: Request,
    subject_id: int = Form(...),
    text: str = Form(...),
    option1: str = Form(...),
    option2: str = Form(...),
    option3: str = Form(""),
    option4: str = Form(""),
    correct_index: int = Form(...),
    explanation: str = Form(""),
    difficulty: str = Form("medium"),
):
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=302)
    options = [o for o in [option1, option2, option3, option4] if o.strip()]
    session = get_session()
    try:
        q = Question(
            subject_id=subject_id, text=text, options=json.dumps(options, ensure_ascii=False),
            correct_index=correct_index, explanation=explanation, difficulty=difficulty,
        )
        session.add(q)
        session.commit()
        return RedirectResponse("/questions", status_code=302)
    finally:
        session.close()


@app.get("/questions/delete/{question_id}")
async def delete_question(request: Request, question_id: int):
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=302)
    session = get_session()
    try:
        q = session.query(Question).filter_by(id=question_id).first()
        if q:
            session.delete(q)
            session.commit()
        return RedirectResponse("/questions", status_code=302)
    finally:
        session.close()


# ---------------- بناء الاختبارات ----------------

@app.get("/exams", response_class=HTMLResponse)
async def exams_page(request: Request):
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=302)
    session = get_session()
    try:
        exams = session.query(Exam).order_by(Exam.id.desc()).all()
        subjects = session.query(Subject).all()
        questions = session.query(Question).all()
        return templates.TemplateResponse(
            "exams.html", {"request": request, "exams": exams, "subjects": subjects, "questions": questions}
        )
    finally:
        session.close()


@app.post("/exams/add")
async def add_exam(
    request: Request,
    title: str = Form(...),
    subject_id: str = Form(""),
    question_ids: list[str] = Form([]),
    time_per_question: int = Form(30),
):
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=302)
    session = get_session()
    try:
        exam = Exam(
            title=title,
            subject_id=int(subject_id) if subject_id else None,
            question_ids=json.dumps([int(i) for i in question_ids]),
            time_per_question=time_per_question,
        )
        session.add(exam)
        session.commit()
        return RedirectResponse("/exams", status_code=302)
    finally:
        session.close()


@app.get("/exams/delete/{exam_id}")
async def delete_exam(request: Request, exam_id: int):
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=302)
    session = get_session()
    try:
        e = session.query(Exam).filter_by(id=exam_id).first()
        if e:
            session.delete(e)
            session.commit()
        return RedirectResponse("/exams", status_code=302)
    finally:
        session.close()


# ---------------- الرسائل المجدولة ----------------

@app.get("/scheduled", response_class=HTMLResponse)
async def scheduled_page(request: Request):
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=302)
    session = get_session()
    try:
        items = session.query(ScheduledMessage).order_by(ScheduledMessage.id.desc()).all()
        return templates.TemplateResponse("scheduled.html", {"request": request, "items": items})
    finally:
        session.close()


@app.post("/scheduled/add")
async def add_scheduled(
    request: Request,
    chat_id: int = Form(...),
    content: str = Form(...),
    mode: str = Form(...),  # once | recurring
    run_at: str = Form(""),
    cron_expr: str = Form(""),
    pin: bool = Form(False),
):
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=302)
    session = get_session()
    try:
        msg = ScheduledMessage(chat_id=chat_id, content=content, pin=pin, is_active=True)
        if mode == "once" and run_at:
            msg.run_at = datetime.datetime.fromisoformat(run_at)
        elif mode == "recurring" and cron_expr:
            msg.cron_expr = cron_expr
        session.add(msg)
        session.commit()

        from scheduler import schedule_message
        if bot_instance:
            schedule_message(bot_instance, msg)

        return RedirectResponse("/scheduled", status_code=302)
    finally:
        session.close()


@app.get("/scheduled/delete/{msg_id}")
async def delete_scheduled(request: Request, msg_id: int):
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=302)
    session = get_session()
    try:
        m = session.query(ScheduledMessage).filter_by(id=msg_id).first()
        if m:
            m.is_active = False
            session.commit()
        return RedirectResponse("/scheduled", status_code=302)
    finally:
        session.close()


# ---------------- إعدادات الجروب ----------------

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=302)
    session = get_session()
    try:
        groups = session.query(GroupSettings).all()
        return templates.TemplateResponse("settings.html", {"request": request, "groups": groups})
    finally:
        session.close()


@app.post("/settings/update/{chat_id}")
async def update_settings(
    request: Request,
    chat_id: int,
    welcome_text: str = Form(""),
    rules_text: str = Form(""),
    max_warnings: int = Form(3),
):
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=302)
    session = get_session()
    try:
        s = session.query(GroupSettings).filter_by(chat_id=chat_id).first()
        if s:
            s.welcome_text = welcome_text
            s.rules_text = rules_text
            s.max_warnings = max_warnings
            session.commit()
        return RedirectResponse("/settings", status_code=302)
    finally:
        session.close()
