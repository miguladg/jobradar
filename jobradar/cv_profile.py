"""
STEP 2 — cv_profile.py
Your CV as structured data.

WHY structured and not a plain text blob: the tools in step 4 need to
*calculate* things from it (skill overlap, years of experience). You can
loop over a list; you cannot loop over a paragraph.

Note each role has a "technologies" list — that is what lets Python
compute real experience years instead of the model guessing.
"""

CV = {
    "name": "Miguel Angel Duarte Galindo",
    "location": "Bogotá, Colombia",
    "open_to": ["Remote", "Hybrid (Bogotá)", "International remote"],
    "target_roles": [
        "Data Analyst",
        "Data Engineer",
        "Backend Developer",
        "Process Automation Engineer",
    ],
    "languages": {
        "Spanish": "Native",
        "English": "B2 — strong reading/writing and technical communication",
    },
    "education": {
        "degree": "B.S. Industrial Engineering",
        "school": "Universidad Católica de Colombia",
        "year": 2019,
    },
    # Canonical skill list — lowercase keys make matching reliable.
    "skills": [
        "python", "pandas", "etl", "sql", "postgresql", "sql server",
        "aws", "aws lambda", "s3", "api gateway", "rds",
        "javascript", "typescript", "node.js", "nestjs", "express",
        "google apps script", "rest api", "git", "github",
        "docker", "azure devops", "qa testing", "beautifulsoup",
    ],
    "experience": [
        {
            "title": "Data Analyst & QA Tester",
            "company": "Secretaría Distrital de Movilidad",
            "start": "2025-02",
            "end": "2026-04",
            "technologies": ["sql", "sql server", "google apps script", "qa testing", "python"],
            "highlights": [
                "Built SQL queries powering reports and dashboards for multiple business areas",
                "Executed functional testing on a third-party supplier application",
                "Automated shipment tracking with Google Apps Script, Drive and Sheets",
            ],
        },
        {
            "title": "Backend Developer & Integrations",
            "company": "DONREP",
            "start": "2023-08",
            "end": "2024-04",
            "technologies": ["node.js", "aws", "aws lambda", "sql", "rest api", "javascript"],
            "highlights": [
                "Built serverless microservices on AWS Lambda and Node.js",
                "Built a REST API connector with Notion to centralize data sync",
                "Designed DB schemas and queries exposed through microservices",
            ],
        },
        {
            "title": "E-Commerce Developer — Process Automation",
            "company": "Somina Odontología",
            "start": "2021-05",
            "end": "2023-05",
            "technologies": ["google apps script", "rest api", "javascript", "sql"],
            "highlights": [
                "Integrated WooCommerce, MercadoLibre and Google Sheets APIs",
                "Built a real-time inventory tool used daily by the operations team",
                "Automated order synchronization with cron scripts",
            ],
        },
    ],
}
