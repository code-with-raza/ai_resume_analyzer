import json
import os
import re
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import HttpResponse
from django.contrib.auth import logout, authenticate, login
from django.contrib.auth.models import User
from django.contrib.auth.forms import AuthenticationForm
from .models import ResumeAnalysis
from groq import Groq

# PDF text extraction library
import pypdf

# ReportLab core layout framework elements
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))


def dashboard_view(request):
    # Fetch past analyses for the logged-in user ordered by date for charts
    past_analyses_queryset = ResumeAnalysis.objects.filter(user=request.user).order_by('created_at') if request.user.is_authenticated else ResumeAnalysis.objects.none()
    
    # Prepare analytical data primitives for Chart.js
    dates = [item.created_at.strftime('%b %d') for item in past_analyses_queryset]
    scores = [item.match_score for item in past_analyses_queryset]
    
    # Count decision distributions
    go_count = past_analyses_queryset.filter(decision='GO').count()
    nogo_count = past_analyses_queryset.filter(decision='NO-GO').count()
    review_count = past_analyses_queryset.filter(decision='MARGINAL').count()

    # Base context initialization (Merged single point of truth)
    context = {
        'extracted_text': request.session.get('cached_extracted_text', ''),
        'filename': request.session.get('cached_filename', 'Document'),
        'ai_analysis': None,
        'match_score': 0,
        'decision': 'MARGINAL',
        'job_description': request.session.get('cached_jd', ''),
        'past_analyses': past_analyses_queryset.order_by('-created_at')[:5], # Keeping history tracking latest-first
        'chart_dates': json.dumps(dates),
        'chart_scores': json.dumps(scores),
        'decision_counts': json.dumps([go_count, nogo_count, review_count]),
    }

    if request.method == "POST":
        # 1. TRACE: Intercept file upload if present (Resolves empty placeholder issue)
        if "resume" in request.FILES:
            uploaded_file = request.FILES["resume"]
            filename = uploaded_file.name
            extracted_text = ""
            
            try:
                # Production-grade memory-buffer parsing via pypdf (No disk writes)
                reader = pypdf.PdfReader(uploaded_file)
                text_runs = []
                for page in reader.pages:
                    text_runs.append(page.extract_text() or "")
                extracted_text = "\n".join(text_runs).strip()
                
                if not extracted_text:
                    messages.warning(request, "Could not extract legible structural text from PDF. It might be scanned or image-heavy.")
                else:
                    messages.success(request, f"Successfully parsed and staging file: {filename}")
                    
            except Exception as e:
                messages.error(request, f"File Engine Failure: {str(e)}")
                
            # Populate operational cache primitives immediately
            request.session['cached_extracted_text'] = extracted_text
            request.session['cached_filename'] = filename
            context['extracted_text'] = extracted_text
            context['filename'] = filename

        # 2. TRACE: Evaluate Core Target Comparison Logic Execution (Sequential pipeline block)
        if "run_ai_analysis" in request.POST or ("resume" in request.FILES and request.POST.get('job_description')):
            extracted_text = request.session.get('cached_extracted_text', '').strip()
            filename = request.session.get('cached_filename', 'Document')
            job_description = request.POST.get('job_description', '').strip()
            
            if not extracted_text:
                messages.error(request, "Pipeline Execution Blocked: Please upload a readable candidate resume asset first.")
                return render(request, "analyzer/dashboard.html", context)
                
            if not job_description:
                messages.error(request, "Pipeline Execution Blocked: Target job specification parameters must be provided.")
                return render(request, "analyzer/dashboard.html", context)

            request.session['cached_jd'] = job_description
            context['job_description'] = job_description
                
            try:
                system_prompt = (
                    "You are an expert corporate technical recruiter and ATS algorithms specialist.\n"
                    "Analyze the provided candidate resume against the target job description to look for structural errors, missing core technical skill sets, and keyword gaps.\n\n"
                    "CRITICAL: You must begin your output with these exact technical metadata tags before any content:\n"
                    "METRIC_SCORE: [Calculate an authentic keyword match percentage between 0 and 100]\n"
                    "METRIC_DECISION: [Write exactly either 'GO', 'NO-GO', or 'MARGINAL']\n\n"
                    "Structure your final report response beautifully using Markdown with these explicit sections:\n"
                    "### 📊 Overall Evaluation & Job Hiring Chances\n"
                    "### ❌ Alignment Errors & Critical Flaws\n"
                    "### 🔍 Missing Core Keywords & Tech Stack Gaps\n"
                    "### 💡 Recommended Structural Adjustments"
                )
                user_payload = f"TARGET JOB DESCRIPTION:\n{job_description}\n\nCANDIDATE RESUME VECTORS:\n{extracted_text}"

                response = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_payload}
                    ],
                    temperature=0.2
                )
                
                raw_output = response.choices[0].message.content
                
                match_score = 50
                decision = 'MARGINAL'
                
                score_match = re.search(r'METRIC_SCORE:\s*(\d+)', raw_output)
                decision_match = re.search(r'METRIC_DECISION:\s*(GO|NO-GO|MARGINAL)', raw_output)
                
                if score_match:
                    match_score = int(score_match.group(1))
                if decision_match:
                    decision = decision_match.group(1)
                    
                clean_markdown = re.sub(r'METRIC_SCORE:\s*\d+\n?', '', raw_output)
                clean_markdown = re.sub(r'METRIC_DECISION:\s*(GO|NO-GO|MARGINAL)\n?', '', clean_markdown).strip()
                
                if request.user.is_authenticated:
                    ResumeAnalysis.objects.create(
                        user=request.user,
                        filename=filename,
                        job_description=job_description[:100] + "...",
                        match_score=match_score,
                        decision=decision,
                        report_markdown=clean_markdown
                    )
                    # Sync UI telemetry state charts live post-transaction
                    updated_qs = ResumeAnalysis.objects.filter(user=request.user).order_by('created_at')
                    context['past_analyses'] = updated_qs.order_by('-created_at')[:5]
                    context['chart_dates'] = json.dumps([item.created_at.strftime('%b %d') for item in updated_qs])
                    context['chart_scores'] = json.dumps([item.match_score for item in updated_qs])
                    context['decision_counts'] = json.dumps([
                        updated_qs.filter(decision='GO').count(),
                        updated_qs.filter(decision='NO-GO').count(),
                        updated_qs.filter(decision='MARGINAL').count()
                    ])
                
                context['ai_analysis'] = clean_markdown
                context['match_score'] = match_score
                context['decision'] = decision
                
                messages.success(request, "Job comparison gap matrix successfully compiled!")
                
            except Exception as e:
                messages.error(request, f"Pipeline Inference Error: {str(e)}")

    return render(request, "analyzer/dashboard.html", context)


def upgrade_cv_view(request):
    resume_text = request.session.get('cached_extracted_text', '')
    job_description = request.session.get('cached_jd', '')

    if not resume_text or not job_description:
        messages.error(request, "Missing structural data context to process the PDF upgrade.")
        return redirect('dashboard')

    try:
        system_prompt = (
            "You are an elite resume architect specialized in single-column modern ATS layouts.\n"
            "Optimize the candidate's resume context to maximize keyword matching against the target job description.\n"
            "CRITICAL: You must return ONLY a clean JSON block. Do not include markdown wraps or system meta messages.\n\n"
            "EXPECTED BLUEPRINT STRUCTURAL SCHEMA:\n"
            "{\n"
            "  \"name\": \"Full Name\",\n"
            "  \"summary\": \"Brief professional introductory profile abstract...\",\n"
            "  \"contact\": { \"email\": \"example@mail.com\", \"phone\": \"0314...\", \"location\": \"City, Country\" },\n"
            "  \"work_experience\": [\n"
            "     { \"company\": \"Company Name\", \"role\": \"Job Title\", \"dates\": \"Month Year - Month Year\", \"bullets\": [\"Action bullet 1\", \"Action bullet 2\"] }\n"
            "  ],\n"
            "  \"education\": [\n"
            "     { \"institution\": \"University/College Name\", \"degree\": \"Degree Name / Major\", \"dates\": \"Year - Year\" }\n"
            "  ],\n"
            "  \"projects\": [\n"
            "     { \"title\": \"Project Identifier Title\", \"dates\": \"Timeline Span\", \"bullets\": [\"Scope development achievement line\", \"Tech integration metric\"] }\n"
            "  ],\n"
            "  \"skills\": [\"Category Title: Skill A, Skill B, Skill C\", \"Tools: Git, VS Code\"],\n"
            "  \"certificates\": [\"Valid Certification Identification Name\"]\n"
            "}"
        )
        user_payload = f"TARGET JOB DESCRIPTION:\n{job_description}\n\nORIGINAL RESUME:\n{resume_text}"

        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload}
            ],
            temperature=0.2
        )
        
        raw_json = response.choices[0].message.content.strip()
        
        if raw_json.startswith("```json"):
            raw_json = raw_json[7:]
        elif raw_json.startswith("```"):
            raw_json = raw_json[3:]
        
        if raw_json.endswith("```"):
            raw_json = raw_json[:-3]
            
        data = json.loads(raw_json.strip())

        pdf_response = HttpResponse(content_type='application/pdf')
        pdf_response['Content-Disposition'] = 'attachment; filename="ATS_Optimized_Resume.pdf"'

        doc = SimpleDocTemplate(
            pdf_response,
            pagesize=letter,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36
        )
        
        styles = getSampleStyleSheet()
        
        name_style = ParagraphStyle(
            'ModernName', fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=HexColor('#0f172a'), spaceAfter=4, alignment=1
        )
        summary_style = ParagraphStyle(
            'ModernSummary', fontName='Helvetica', fontSize=9.5, leading=14, textColor=HexColor('#334155'), spaceAfter=6, alignment=4
        )
        contact_style = ParagraphStyle(
            'ModernContact', fontName='Helvetica', fontSize=9, leading=12, textColor=HexColor('#0284c7'), alignment=1
        )
        section_heading = ParagraphStyle(
            'ModernSection', fontName='Helvetica-Bold', fontSize=10.5, leading=14, textColor=HexColor('#0284c7'), spaceBefore=10, spaceAfter=4, keepWithNext=True
        )
        left_bold = ParagraphStyle(
            'LeftBoldText', fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=HexColor('#0f172a')
        )
        left_sub = ParagraphStyle(
            'LeftSubText', fontName='Helvetica', fontSize=9.5, leading=13, textColor=HexColor('#1e293b')
        )
        right_date = ParagraphStyle(
            'RightDateText', fontName='Helvetica', fontSize=9, leading=13, textColor=HexColor('#475569'), alignment=2
        )
        bullet_style = ParagraphStyle(
            'ModernBullet', fontName='Helvetica', fontSize=9.5, leading=13.5, textColor=HexColor('#334155'), leftIndent=12, firstLineIndent=-12, spaceAfter=2
        )

        story = []
        story.append(Paragraph(data.get('name', 'Candidate Name'), name_style))
        story.append(Paragraph(data.get('summary', ''), summary_style))
        
        c = data.get('contact', {})
        contact_line = f"✉️ {c.get('email','')}   |   📞 {c.get('phone','')}   |   📍 {c.get('location','')}"
        story.append(Paragraph(contact_line, contact_style))
        story.append(Spacer(1, 4))

        def build_split_row(left_string, right_string, left_fmt, right_fmt):
            p_left = Paragraph(left_string, left_fmt)
            p_right = Paragraph(right_string, right_fmt)
            t = Table([[p_left, p_right]], colWidths=[380, 160])
            t.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('LEFTPADDING', (0,0), (-1,-1), 0),
                ('RIGHTPADDING', (0,0), (-1,-1), 0),
                ('BOTTOMPADDING', (0,0), (-1,-1), 1),
                ('TOPPADDING', (0,0), (-1,-1), 1),
            ]))
            return t

        def build_divider_line():
            t = Table([['']], colWidths=[540], rowHeights=[1])
            t.setStyle(TableStyle([
                ('LINEBELOW', (0,0), (-1,-1), 0.75, HexColor('#cbd5e1')),
                ('BOTTOMPADDING', (0,0), (-1,-1), 0),
                ('TOPPADDING', (0,0), (-1,-1), 0),
            ]))
            return t

        if data.get('work_experience'):
            story.append(Paragraph("WORK EXPERIENCE", section_heading))
            story.append(build_divider_line())
            story.append(Spacer(1, 4))
            for job in data['work_experience']:
                story.append(build_split_row(job.get('company', ''), job.get('dates', ''), left_bold, right_date))
                story.append(Paragraph(job.get('role', ''), left_sub))
                for bullet in job.get('bullets', []):
                    story.append(Paragraph(f"• {bullet}", bullet_style))
                story.append(Spacer(1, 4))

        if data.get('education'):
            story.append(Paragraph("EDUCATION", section_heading))
            story.append(build_divider_line())
            story.append(Spacer(1, 4))
            for edu in data['education']:
                story.append(build_split_row(edu.get('institution', ''), edu.get('dates', ''), left_bold, right_date))
                story.append(Paragraph(edu.get('degree', ''), left_sub))
                story.append(Spacer(1, 4))

        if data.get('projects'):
            story.append(Paragraph("PROJECTS", section_heading))
            story.append(build_divider_line())
            story.append(Spacer(1, 4))
            for proj in data['projects']:
                story.append(build_split_row(proj.get('title', ''), proj.get('dates', ''), left_bold, right_date))
                for bullet in proj.get('bullets', []):
                    story.append(Paragraph(f"• {bullet}", bullet_style))
                story.append(Spacer(1, 4))

        if data.get('skills'):
            story.append(Paragraph("SKILLS", section_heading))
            story.append(build_divider_line())
            story.append(Spacer(1, 4))
            for skill in data['skills']:
                story.append(Paragraph(f"• {skill}", bullet_style))
            story.append(Spacer(1, 4))

        if data.get('certificates'):
            story.append(Paragraph("CERTIFICATE", section_heading))
            story.append(build_divider_line())
            story.append(Spacer(1, 4))
            for cert in data['certificates']:
                story.append(Paragraph(f"• {cert}", bullet_style))

        doc.build(story)
        return pdf_response

    except Exception as e:
        messages.error(request, f"PDF Engine Compilation Error: {str(e)}")
        return redirect('dashboard')


def register_view(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        
        if not username or not email or not password:
            messages.error(request, "Registration blocked: Verify parameter inputs fail safety limits.")
            return render(request, "analyzer/register.html")
        
        # Check if user already exists
        if User.objects.filter(username=username).exists():
            messages.error(request, "Registration blocked: Username is already taken.")
            return render(request, "analyzer/register.html")
            
        try:
            # Securely create user and set encrypted password
            user = User.objects.create_user(username=username, email=email, password=password)
            user.save()
            
            # Auto-authenticate and log the user in instantly post-registration
            authenticated_user = authenticate(username=username, password=password)
            if authenticated_user is not None:
                login(request, authenticated_user)
                
            messages.success(request, f"Account created successfully! Welcome, {username}!")
            return redirect('dashboard')
            
        except Exception as e:
            messages.error(request, f"Database Write Failure: {str(e)}")
            return render(request, "analyzer/register.html")
            
    return render(request, "analyzer/register.html")


def login_view(request):
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f"Authentication complete. Welcome back, {username}!")
                return redirect('dashboard')
        else:
            messages.error(request, "Invalid username or password credentials.")
    else:
        form = AuthenticationForm()
    return render(request, "analyzer/login.html", {"form": form})


def logout_view(request):
    logout(request)
    messages.info(request, "Session closed successfully.")
    return redirect('login')