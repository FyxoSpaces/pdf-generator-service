"""
Clara Health PDF Generator - FastAPI Microservice
Standalone service for generating health report PDFs
WITH S3 UPLOAD INTEGRATION + PROGRESS TRACKING + CAMPDATA VALIDATION
Version 2.1.0
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import uvicorn
import os
import json
import sys
from datetime import datetime
import shutil
from pathlib import Path
import requests

# Add the current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from master_generator import generate_complete_health_report
from s3_uploader import S3Uploader

app = FastAPI(
    title="Clara Health PDF Generator",
    description="Microservice for generating student health report PDFs with S3 upload and progress tracking",
    version="2.1.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this properly in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
BACKGROUNDS_FOLDER = os.path.join(os.path.dirname(__file__), "backgrounds")
FONTS_FOLDER = os.path.join(os.path.dirname(__file__), "fonts")
OUTPUT_FOLDER = os.path.join(os.path.dirname(__file__), "generated-reports")
TEMP_FOLDER = os.path.join(os.path.dirname(__file__), "temp")

# S3 Configuration
S3_BUCKET = os.getenv("S3_BUCKET", "pdf-clarahealtonation")
S3_REGION = os.getenv("S3_REGION", "ap-south-1")
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

# NEW: Progress API Configuration
PROGRESS_API_URL = "https://api.clarahealtonation.in/v1/reports/report-loading-progress"
STATIC_TOKEN = "zX3N9pV4tQ7bL2yH8kC5wR1sD6fG0jK3mP8vB4nT6yU9iE2oA"

# Initialize S3 Uploader
s3_uploader = S3Uploader(
    bucket_name=S3_BUCKET,
    region=S3_REGION,
    aws_access_key=AWS_ACCESS_KEY,
    aws_secret_key=AWS_SECRET_KEY
)

# Create necessary folders
for folder in [OUTPUT_FOLDER, TEMP_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# Pydantic models
class StudentReportRequest(BaseModel):
    """Request model for generating a single PDF"""
    data: Dict[str, Any]  # Full production JSON structure

class BatchReportRequest(BaseModel):
    """Request model for batch PDF generation"""
    reports: List[Dict[str, Any]]  # List of production JSON structures

class HealthCheckResponse(BaseModel):
    """Health check response"""
    status: str
    service: str
    version: str
    timestamp: str
    dependencies: Dict[str, bool]

class StudentIdsRequest(BaseModel):
    """Request model for student IDs with progress tracking"""
    studentIds: List[int]
    uniqueLoaderKey: str  # NEW: Required for progress tracking
    nodeApiUrl: str = "https://api.clarahealtonation.in/v1/reports/data/multiple"
    authToken: Optional[str] = STATIC_TOKEN  # Uses static token by default

# Helper functions
def check_dependencies() -> Dict[str, bool]:
    """Check if all required Python packages are installed"""
    dependencies = {}
    required_packages = ["reportlab", "PIL", "PyPDF2"]
    
    for package in required_packages:
        try:
            __import__(package)
            dependencies[package] = True
        except ImportError:
            dependencies[package] = False
    
    return dependencies

def get_output_filename(student_data: Dict) -> str:
    """Generate output filename from student data"""
    clara_id = student_data.get('claraId', 'unknown')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f"{clara_id}_health_report_{timestamp}.pdf"

def cleanup_old_files(folder: str, max_age_hours: int = 24):
    """Clean up files older than specified hours"""
    try:
        now = datetime.now().timestamp()
        for file in os.listdir(folder):
            file_path = os.path.join(folder, file)
            if os.path.isfile(file_path):
                file_age = now - os.path.getmtime(file_path)
                if file_age > (max_age_hours * 3600):
                    os.remove(file_path)
    except Exception as e:
        print(f"Error cleaning up old files: {e}")

# NEW: campData validation function
def validate_camp_data(camp_data: List) -> bool:
    """
    Validate if campData has actual health screening data
    Returns False if campData is empty or None
    """
    if not camp_data or len(camp_data) == 0:
        return False
    return True

# NEW: Progress tracking function
def send_progress_update(unique_loader_key: str, student_id: int, success: bool, pdf_url: str = None, error: str = None):
    """
    Send progress update to Node.js backend after each report
    
    Args:
        unique_loader_key: Unique key for tracking this batch
        student_id: Student ID being processed
        success: Whether PDF generation succeeded
        pdf_url: S3 URL of generated PDF (if success=True)
        error: Error message (if success=False)
    """
    try:
        payload = {
            "uniqueLoaderKey": unique_loader_key,
            "studentId": student_id,
            "success": success
        }
        
        if success and pdf_url:
            payload["pdfUrl"] = pdf_url
        
        if not success and error:
            payload["error"] = error
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {STATIC_TOKEN}'
        }
        
        print(f"   📡 Sending progress update for student {student_id}...")
        
        response = requests.post(
            PROGRESS_API_URL,
            headers=headers,
            json=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            print(f"   ✅ Progress update sent successfully")
        else:
            print(f"   ⚠️  Progress update failed (status {response.status_code})")
        
    except Exception as e:
        print(f"   ❌ Error sending progress update: {str(e)}")
        # Don't raise - progress update failure shouldn't stop PDF generation

# API Endpoints

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Clara Health PDF Generator",
        "version": "2.1.0",
        "status": "running",
        "features": [
            "campData validation",
            "Progress tracking",
            "S3 upload",
            "Batch processing"
        ],
        "endpoints": {
            "health": "/health",
            "generate": "/api/generate-pdf",
            "batch": "/api/generate-batch",
            "generate-and-upload": "/api/generate-and-upload",
            "generate-by-ids": "/api/generate-by-ids",
            "download": "/api/download/{filename}"
        }
    }

@app.get("/health", response_model=HealthCheckResponse)
async def health_check():
    """Health check endpoint"""
    dependencies = check_dependencies()
    all_healthy = all(dependencies.values())
    
    # Check if required folders exist
    folders_exist = all([
        os.path.exists(BACKGROUNDS_FOLDER),
        os.path.exists(FONTS_FOLDER)
    ])
    
    return HealthCheckResponse(
        status="healthy" if (all_healthy and folders_exist) else "unhealthy",
        service="Clara Health PDF Generator",
        version="2.1.0",
        timestamp=datetime.now().isoformat(),
        dependencies=dependencies
    )

@app.post("/api/generate-pdf")
async def generate_pdf(request: StudentReportRequest, background_tasks: BackgroundTasks):
    """
    Generate a single PDF report
    
    Request body should contain the full production JSON structure:
    {
        "data": {
            "student": {...},
            "campData": [...],
            "school": {...}
        }
    }
    """
    try:
        print("\n🏥 Received PDF generation request")
        
        # Validate input
        if not request.data or 'student' not in request.data.get('data', {}):
            raise HTTPException(
                status_code=400,
                detail="Invalid request: missing student data"
            )
        
        student_data = request.data['data']['student']
        student_name = student_data.get('name', 'Unknown')
        clara_id = student_data.get('claraId', 'unknown')
        
        print(f"📋 Generating PDF for: {student_name} ({clara_id})")
        
        # Generate output filename
        output_filename = get_output_filename(student_data)
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        
        # Create temporary JSON file
        temp_json_path = os.path.join(TEMP_FOLDER, f"temp_{clara_id}_{datetime.now().timestamp()}.json")
        with open(temp_json_path, 'w') as f:
            json.dump(request.data, f)
        
        # Generate PDF
        generate_complete_health_report(
            json_path=temp_json_path,
            backgrounds_folder=BACKGROUNDS_FOLDER,
            output_path=output_path,
            fonts_folder=FONTS_FOLDER
        )
        
        # Clean up temp file
        if os.path.exists(temp_json_path):
            os.remove(temp_json_path)
        
        # Check if PDF was created
        if not os.path.exists(output_path):
            raise HTTPException(
                status_code=500,
                detail="PDF generation failed - file not created"
            )
        
        print(f"✅ PDF generated: {output_filename}")
        
        # Schedule cleanup of old files in background
        background_tasks.add_task(cleanup_old_files, OUTPUT_FOLDER)
        
        return {
            "success": True,
            "message": "PDF generated successfully",
            "data": {
                "filename": output_filename,
                "studentName": student_name,
                "claraId": clara_id,
                "downloadUrl": f"/api/download/{output_filename}",
                "fileSize": os.path.getsize(output_path),
                "generatedAt": datetime.now().isoformat()
            }
        }
        
    except Exception as e:
        print(f"❌ Error generating PDF: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"PDF generation failed: {str(e)}"
        )

@app.post("/api/generate-batch")
async def generate_batch_pdfs(request: BatchReportRequest, background_tasks: BackgroundTasks):
    """
    Generate multiple PDF reports in batch
    
    Request body:
    {
        "reports": [
            { "data": { "student": {...}, "campData": [...], "school": {...} } },
            { "data": { "student": {...}, "campData": [...], "school": {...} } }
        ]
    }
    """
    try:
        print(f"\n📦 Batch generation request for {len(request.reports)} reports")
        
        results = {
            "success": [],
            "failed": []
        }
        
        for idx, report_data in enumerate(request.reports, 1):
            try:
                print(f"\n[{idx}/{len(request.reports)}] Processing...")
                
                student_data = report_data['data']['student']
                student_name = student_data.get('name', 'Unknown')
                clara_id = student_data.get('claraId', 'unknown')
                
                # Generate output filename
                output_filename = get_output_filename(student_data)
                output_path = os.path.join(OUTPUT_FOLDER, output_filename)
                
                # Create temporary JSON file
                temp_json_path = os.path.join(TEMP_FOLDER, f"temp_{clara_id}_{datetime.now().timestamp()}.json")
                with open(temp_json_path, 'w') as f:
                    json.dump(report_data, f)
                
                # Generate PDF
                generate_complete_health_report(
                    json_path=temp_json_path,
                    backgrounds_folder=BACKGROUNDS_FOLDER,
                    output_path=output_path,
                    fonts_folder=FONTS_FOLDER
                )
                
                # Clean up temp file
                if os.path.exists(temp_json_path):
                    os.remove(temp_json_path)
                
                results["success"].append({
                    "studentName": student_name,
                    "claraId": clara_id,
                    "filename": output_filename,
                    "downloadUrl": f"/api/download/{output_filename}"
                })
                
                print(f"✅ [{idx}/{len(request.reports)}] Success: {student_name}")
                
            except Exception as e:
                print(f"❌ [{idx}/{len(request.reports)}] Failed: {str(e)}")
                results["failed"].append({
                    "studentName": student_data.get('name', 'Unknown'),
                    "claraId": student_data.get('claraId', 'unknown'),
                    "error": str(e)
                })
        
        # Schedule cleanup
        background_tasks.add_task(cleanup_old_files, OUTPUT_FOLDER)
        
        print(f"\n📊 Batch complete: {len(results['success'])} succeeded, {len(results['failed'])} failed")
        
        return {
            "success": True,
            "message": "Batch generation completed",
            "data": {
                "total": len(request.reports),
                "successCount": len(results["success"]),
                "failedCount": len(results["failed"]),
                "results": results
            }
        }
        
    except Exception as e:
        print(f"❌ Batch generation error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Batch generation failed: {str(e)}"
        )

@app.get("/api/download/{filename}")
async def download_pdf(filename: str):
    """Download a generated PDF file"""
    try:
        file_path = os.path.join(OUTPUT_FOLDER, filename)
        
        if not os.path.exists(file_path):
            raise HTTPException(
                status_code=404,
                detail=f"File not found: {filename}"
            )
        
        return FileResponse(
            path=file_path,
            media_type="application/pdf",
            filename=filename
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error downloading file: {str(e)}"
        )

@app.delete("/api/cleanup")
async def cleanup_generated_files(max_age_hours: int = 24):
    """Manually trigger cleanup of old generated files"""
    try:
        cleanup_old_files(OUTPUT_FOLDER, max_age_hours)
        cleanup_old_files(TEMP_FOLDER, 1)  # Clean temp files older than 1 hour
        
        return {
            "success": True,
            "message": f"Cleaned up files older than {max_age_hours} hours"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Cleanup failed: {str(e)}"
        )

@app.get("/api/list-reports")
async def list_generated_reports():
    """List all generated PDF reports"""
    try:
        files = []
        for filename in os.listdir(OUTPUT_FOLDER):
            if filename.endswith('.pdf'):
                file_path = os.path.join(OUTPUT_FOLDER, filename)
                files.append({
                    "filename": filename,
                    "size": os.path.getsize(file_path),
                    "createdAt": datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat(),
                    "downloadUrl": f"/api/download/{filename}"
                })
        
        return {
            "success": True,
            "data": {
                "count": len(files),
                "files": sorted(files, key=lambda x: x['createdAt'], reverse=True)
            }
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error listing files: {str(e)}"
        )

# ==================== S3 INTEGRATED ENDPOINTS ====================

@app.post("/api/generate-and-upload")
async def generate_and_upload_pdf(request: StudentReportRequest, background_tasks: BackgroundTasks):
    """
    Generate PDF and upload to S3 (Single Student)
    
    Returns S3 URL instead of local file path
    """
    try:
        print("\n🏥 [S3 MODE] Received PDF generation request")
        
        # Validate input
        if not request.data or 'student' not in request.data.get('data', {}):
            raise HTTPException(
                status_code=400,
                detail="Invalid request: missing student data"
            )
        
        student_data = request.data['data']['student']
        student_name = student_data.get('name', 'Unknown')
        clara_id = student_data.get('claraId', 'unknown')
        
        print(f"📋 Generating PDF for: {student_name} ({clara_id})")
        
        # Generate output filename
        output_filename = get_output_filename(student_data)
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        
        # Create temporary JSON file
        temp_json_path = os.path.join(TEMP_FOLDER, f"temp_{clara_id}_{datetime.now().timestamp()}.json")
        with open(temp_json_path, 'w') as f:
            json.dump(request.data, f)
        
        # Generate PDF
        generate_complete_health_report(
            json_path=temp_json_path,
            backgrounds_folder=BACKGROUNDS_FOLDER,
            output_path=output_path,
            fonts_folder=FONTS_FOLDER
        )
        
        # Clean up temp file
        if os.path.exists(temp_json_path):
            os.remove(temp_json_path)
        
        # Check if PDF was created
        if not os.path.exists(output_path):
            raise HTTPException(
                status_code=500,
                detail="PDF generation failed - file not created"
            )
        
        print(f"✅ PDF generated: {output_filename}")
        
        # Upload to S3
        print(f"📤 Uploading to S3...")
        s3_result = s3_uploader.upload_pdf(
            file_path=output_path,
            s3_key=output_filename,
            make_public=False  # Use presigned URLs
        )
        
        if not s3_result.get('success'):
            raise HTTPException(
                status_code=500,
                detail=f"S3 upload failed: {s3_result.get('error')}"
            )
        
        print(f"✅ Uploaded to S3: {s3_result['s3_key']}")
        
        # Schedule cleanup of local file in background
        background_tasks.add_task(os.remove, output_path)
        background_tasks.add_task(cleanup_old_files, OUTPUT_FOLDER)
        
        return {
            "success": True,
            "message": "PDF generated and uploaded to S3 successfully",
            "data": {
                "studentName": student_name,
                "claraId": clara_id,
                "pdfUrl": s3_result['s3_url'],
                "s3Key": s3_result['s3_key'],
                "s3Bucket": s3_result['bucket'],
                "generatedAt": datetime.now().isoformat()
            }
        }
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"PDF generation/upload failed: {str(e)}"
        )

@app.post("/api/generate-and-upload-batch")
async def generate_and_upload_batch(request: BatchReportRequest, background_tasks: BackgroundTasks):
    """
    Generate multiple PDFs and upload all to S3 (Batch Processing)
    
    Request body:
    {
        "reports": [
            { "data": { "student": {...}, "campData": [...], "school": {...} } },
            { "data": { "student": {...}, "campData": [...], "school": {...} } }
        ]
    }
    """
    try:
        print(f"\n📦 [S3 BATCH MODE] Processing {len(request.reports)} reports")
        
        results = {
            "success": [],
            "failed": []
        }
        
        for idx, report_data in enumerate(request.reports, 1):
            try:
                print(f"\n[{idx}/{len(request.reports)}] Processing...")
                
                student_data = report_data['data']['student']
                student_id = student_data.get('id', 0)
                student_name = student_data.get('name', 'Unknown')
                clara_id = student_data.get('claraId', 'unknown')
                
                # Generate output filename
                output_filename = get_output_filename(student_data)
                output_path = os.path.join(OUTPUT_FOLDER, output_filename)
                
                # Create temporary JSON file
                temp_json_path = os.path.join(TEMP_FOLDER, f"temp_{clara_id}_{datetime.now().timestamp()}.json")
                with open(temp_json_path, 'w') as f:
                    json.dump(report_data, f)
                
                # Generate PDF
                generate_complete_health_report(
                    json_path=temp_json_path,
                    backgrounds_folder=BACKGROUNDS_FOLDER,
                    output_path=output_path,
                    fonts_folder=FONTS_FOLDER
                )
                
                # Clean up temp file
                if os.path.exists(temp_json_path):
                    os.remove(temp_json_path)
                
                # Upload to S3
                print(f"📤 [{idx}/{len(request.reports)}] Uploading to S3...")
                s3_result = s3_uploader.upload_pdf(
                    file_path=output_path,
                    s3_key=output_filename,
                    make_public=False
                )
                
                if s3_result.get('success'):
                    results["success"].append({
                        "studentId": student_id,
                        "studentName": student_name,
                        "claraId": clara_id,
                        "pdfUrl": s3_result['s3_url'],
                        "s3Key": s3_result['s3_key'],
                        "status": "success"
                    })
                    
                    # Clean up local file
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    
                    print(f"✅ [{idx}/{len(request.reports)}] Success: {student_name}")
                else:
                    raise Exception(s3_result.get('error', 'S3 upload failed'))
                
            except Exception as e:
                error_msg = str(e)
                print(f"❌ [{idx}/{len(request.reports)}] Failed: {error_msg}")
                
                results["failed"].append({
                    "studentId": student_data.get('id', 0),
                    "studentName": student_data.get('name', 'Unknown'),
                    "claraId": student_data.get('claraId', 'unknown'),
                    "error": error_msg,
                    "status": "failed"
                })
        
        # Schedule cleanup
        background_tasks.add_task(cleanup_old_files, OUTPUT_FOLDER)
        background_tasks.add_task(cleanup_old_files, TEMP_FOLDER, 1)
        
        print(f"\n📊 Batch complete: {len(results['success'])} succeeded, {len(results['failed'])} failed")
        
        return {
            "success": True,
            "message": "Batch processing completed",
            "data": {
                "total": len(request.reports),
                "successCount": len(results["success"]),
                "failedCount": len(results["failed"]),
                "results": results["success"],
                "errors": results["failed"]
            }
        }
        
    except Exception as e:
        print(f"❌ Batch processing error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Batch processing failed: {str(e)}"
        )

@app.get("/api/s3/check")
async def check_s3_access():
    """Check S3 bucket access"""
    result = s3_uploader.check_bucket_access()
    if result.get('success'):
        return result
    else:
        raise HTTPException(
            status_code=503,
            detail=result.get('error', 'S3 bucket not accessible')
        )

# ==================== UPDATED: GENERATE BY STUDENT IDS WITH PROGRESS TRACKING ====================

@app.post("/api/generate-by-ids")
async def generate_pdfs_by_student_ids(request: StudentIdsRequest, background_tasks: BackgroundTasks):
    """
    Generate PDFs with campData validation and progress tracking (UPDATED v2.1)
    
    NEW FEATURES:
    - Validates campData before generating (skips if empty)
    - Sends progress update after EACH report (success/failure/skip)
    - Tracks failed and skipped reports separately
    - Continues processing even if individual reports fail
    
    Request:
    {
        "studentIds": [7, 8, 549],
        "uniqueLoaderKey": "loader-key-123",  // REQUIRED for progress tracking
        "authToken": "optional-token"  // Uses static token if not provided
    }
    
    Response:
    {
        "success": true,
        "data": {
            "total": 3,
            "successCount": 2,
            "failedCount": 0,
            "skippedCount": 1,
            "results": [...],  // Successful PDFs
            "errors": [],      // Failed PDFs
            "skipped": []      // Skipped (no campData)
        }
    }
    """
    try:
        import requests
        
        print("\n" + "="*60)
        print(f"📦 [GENERATE BY IDS v2.1] Batch processing started")
        print(f"👥 Student IDs: {request.studentIds}")
        print(f"🔑 Loader Key: {request.uniqueLoaderKey}")
        print("="*60 + "\n")
        
        # Step 1: Fetch data from Node.js API
        print(f"📡 Fetching data for {len(request.studentIds)} students...")
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f"Bearer {request.authToken}"
        }
        
        payload = {'studentId': request.studentIds}
        
        response = requests.post(
            request.nodeApiUrl, 
            headers=headers, 
            json=payload,
            timeout=30
        )
        
        print(f"📥 Response status: {response.status_code}\n")
        
        if response.status_code != 200:
            raise Exception(f"Node API returned status {response.status_code}: {response.text}")
        
        api_response = response.json()
        
        if not api_response.get('success'):
            raise Exception(f"Node API error: {api_response.get('message', 'Unknown error')}")
        
        students_data = api_response.get('data', {}).get('studentsData', [])
        
        if not students_data:
            raise Exception("No student data returned from Node API")
        
        print(f"✅ Fetched data for {len(students_data)} student(s)\n")
        
        # Step 2: Process each student with campData validation and progress tracking
        results = {
            "success": [],
            "failed": [],
            "skipped": []
        }
        
        for idx, student_raw_data in enumerate(students_data, 1):
            student_id = None
            student_name = "Unknown"
            clara_id = "unknown"
            
            try:
                print(f"{'='*60}")
                print(f"[{idx}/{len(students_data)}] Processing student...")
                print(f"{'='*60}")
                
                # Extract student info
                student_info = student_raw_data.get('student', {})
                student_id = student_info.get('id', 0)
                student_name = student_info.get('name', 'Unknown')
                clara_id = student_info.get('claraId', 'unknown')
                
                print(f"📋 Student ID: {student_id}")
                print(f"👤 Name: {student_name}")
                print(f"🆔 Clara ID: {clara_id}")
                
                # CRITICAL: Validate campData
                camp_data = student_raw_data.get('campData', [])
                
                if not validate_camp_data(camp_data):
                    skip_reason = "No health screening data available (campData is empty)"
                    print(f"⚠️  SKIPPING: {skip_reason}")
                    
                    results["skipped"].append({
                        "studentId": student_id,
                        "studentName": student_name,
                        "claraId": clara_id,
                        "reason": skip_reason,
                        "status": "skipped"
                    })
                    
                    # Send progress update - SKIPPED
                    send_progress_update(
                        unique_loader_key=request.uniqueLoaderKey,
                        student_id=student_id,
                        success=False,
                        error=skip_reason
                    )
                    
                    print(f"✅ [{idx}/{len(students_data)}] SKIPPED\n")
                    continue  # Skip this student
                
                print(f"✅ campData validated ({len(camp_data)} records found)")
                
                # Generate output filename
                output_filename = get_output_filename(student_info)
                output_path = os.path.join(OUTPUT_FOLDER, output_filename)
                
                # Create temporary JSON file
                wrapped_data = {"data": student_raw_data}
                temp_json_path = os.path.join(TEMP_FOLDER, f"temp_{clara_id}_{datetime.now().timestamp()}.json")
                with open(temp_json_path, 'w') as f:
                    json.dump(wrapped_data, f)
                
                # Generate PDF
                print(f"🎨 Generating PDF...")
                generate_complete_health_report(
                    json_path=temp_json_path,
                    backgrounds_folder=BACKGROUNDS_FOLDER,
                    output_path=output_path,
                    fonts_folder=FONTS_FOLDER
                )
                
                # Clean up temp file
                if os.path.exists(temp_json_path):
                    os.remove(temp_json_path)
                
                print(f"✅ PDF generated")
                
                # Upload to S3
                print(f"📤 Uploading to S3...")
                s3_result = s3_uploader.upload_pdf(
                    file_path=output_path,
                    s3_key=output_filename,
                    make_public=False
                )
                
                if not s3_result.get('success'):
                    raise Exception(f"S3 upload failed: {s3_result.get('error')}")
                
                pdf_url = s3_result['s3_url']
                print(f"✅ Uploaded to S3")
                
                # Success! Add to results
                results["success"].append({
                    "studentId": student_id,
                    "studentName": student_name,
                    "claraId": clara_id,
                    "pdfUrl": pdf_url,
                    "s3Key": s3_result['s3_key'],
                    "status": "success"
                })
                
                # Send progress update - SUCCESS
                send_progress_update(
                    unique_loader_key=request.uniqueLoaderKey,
                    student_id=student_id,
                    success=True,
                    pdf_url=pdf_url
                )
                
                # Clean up local file
                if os.path.exists(output_path):
                    os.remove(output_path)
                
                print(f"✅ [{idx}/{len(students_data)}] COMPLETED\n")
                
            except Exception as e:
                error_msg = str(e)
                print(f"❌ FAILED: {error_msg}")
                
                results["failed"].append({
                    "studentId": student_id or 0,
                    "studentName": student_name,
                    "claraId": clara_id,
                    "error": error_msg,
                    "status": "failed"
                })
                
                # Send progress update - FAILED
                send_progress_update(
                    unique_loader_key=request.uniqueLoaderKey,
                    student_id=student_id or 0,
                    success=False,
                    error=error_msg
                )
                
                print(f"❌ [{idx}/{len(students_data)}] FAILED\n")
                continue  # Continue with next student
        
        # Schedule cleanup
        background_tasks.add_task(cleanup_old_files, OUTPUT_FOLDER)
        background_tasks.add_task(cleanup_old_files, TEMP_FOLDER, 1)
        
        # Final summary
        print("="*60)
        print("📊 BATCH PROCESSING COMPLETE")
        print("="*60)
        print(f"✅ Success: {len(results['success'])}")
        print(f"❌ Failed: {len(results['failed'])}")
        print(f"⚠️  Skipped: {len(results['skipped'])}")
        print(f"📊 Total: {len(request.studentIds)}")
        print("="*60 + "\n")
        
        return {
            "success": True,
            "message": "PDF generation completed",
            "data": {
                "total": len(request.studentIds),
                "successCount": len(results["success"]),
                "failedCount": len(results["failed"]),
                "skippedCount": len(results["skipped"]),
                "results": results["success"],
                "errors": results["failed"],
                "skipped": results["skipped"]
            }
        }
        
    except requests.RequestException as e:
        error_msg = f"Failed to fetch data from Node API: {str(e)}"
        print(f"\n❌ {error_msg}\n")
        raise HTTPException(
            status_code=500,
            detail=error_msg
        )
        
    except Exception as e:
        print(f"\n❌ Batch generation error: {str(e)}\n")
        raise HTTPException(
            status_code=500,
            detail=f"Batch generation failed: {str(e)}"
        )

if __name__ == "__main__":
    print("\n" + "="*60)
    print("🏥 Clara Health PDF Generator - FastAPI Microservice v2.1")
    print("="*60)
    print(f"\n📁 Backgrounds folder: {BACKGROUNDS_FOLDER}")
    print(f"📁 Fonts folder: {FONTS_FOLDER}")
    print(f"📁 Output folder: {OUTPUT_FOLDER}")
    print(f"\n☁️  S3 Bucket: {S3_BUCKET}")
    print(f"☁️  S3 Region: {S3_REGION}")
    print(f"\n📡 Progress API: {PROGRESS_API_URL}")
    print(f"\n🚀 Starting server on port 8002...")
    print("="*60 + "\n")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8002,
        log_level="info"
    )