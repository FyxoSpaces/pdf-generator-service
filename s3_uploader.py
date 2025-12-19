"""
S3 Upload Utility for Clara Health PDF Reports
"""

import boto3
from botocore.exceptions import ClientError
import os
from datetime import datetime
from typing import Optional

class S3Uploader:
    """Handle S3 uploads for PDF reports"""
    
    def __init__(
        self,
        bucket_name: str = "pdf-clarahealtonation",
        region: str = "ap-south-1",
        aws_access_key: Optional[str] = None,
        aws_secret_key: Optional[str] = None
    ):
        self.bucket_name = bucket_name
        self.region = region
        
        # Initialize S3 client
        session_kwargs = {'region_name': region}
        
        # Only use provided credentials if both are present
        # Otherwise, boto3 will automatically use IAM role (on EC2) or env variables
        if aws_access_key and aws_secret_key:
            session_kwargs['aws_access_key_id'] = aws_access_key
            session_kwargs['aws_secret_access_key'] = aws_secret_key
            print(f"🔑 Using provided AWS credentials")
        else:
            print(f"🔑 Using IAM role / environment credentials")
        
        self.s3_client = boto3.client('s3', **session_kwargs)
        
        print(f"✅ S3 Client initialized for bucket: {bucket_name} in region: {region}")
    
    def upload_pdf(
        self,
        file_path: str,
        s3_key: Optional[str] = None,
        make_public: bool = False
    ) -> dict:
        """
        Upload PDF to S3 bucket
        
        Args:
            file_path: Local path to PDF file
            s3_key: S3 key (path in bucket). If None, auto-generates from filename
            make_public: Whether to make file publicly accessible
            
        Returns:
            dict with s3_url, s3_key, and other metadata
        """
        try:
            # Auto-generate S3 key if not provided
            if not s3_key:
                filename = os.path.basename(file_path)
                s3_key = f"pdfs/{filename}"
            else:
                # Ensure it starts with pdfs/
                if not s3_key.startswith("pdfs/"):
                    s3_key = f"pdfs/{s3_key}"
            
            # Upload file
            extra_args = {
                'ContentType': 'application/pdf',
            }
            
            if make_public:
                extra_args['ACL'] = 'public-read'
            
            print(f"📤 Uploading to S3: {s3_key}")
            
            self.s3_client.upload_file(
                Filename=file_path,
                Bucket=self.bucket_name,
                Key=s3_key,
                ExtraArgs=extra_args
            )
            
            # Generate URL
            if make_public:
                s3_url = f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{s3_key}"
            else:
                # Generate presigned URL (valid for 7 days)
                s3_url = self.s3_client.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': self.bucket_name, 'Key': s3_key},
                    ExpiresIn=604800  # 7 days
                )
            
            print(f"✅ Upload successful: {s3_key}")
            
            return {
                "success": True,
                "s3_url": s3_url,
                "s3_key": s3_key,
                "bucket": self.bucket_name,
                "region": self.region,
                "uploaded_at": datetime.now().isoformat()
            }
            
        except FileNotFoundError:
            print(f"❌ File not found: {file_path}")
            return {
                "success": False,
                "error": f"File not found: {file_path}"
            }
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            print(f"❌ S3 Upload Error ({error_code}): {error_message}")
            return {
                "success": False,
                "error": f"S3 Error: {error_message}"
            }
            
        except Exception as e:
            print(f"❌ Upload failed: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def delete_pdf(self, s3_key: str) -> bool:
        """Delete PDF from S3 bucket"""
        try:
            # Ensure it starts with pdfs/
            if not s3_key.startswith("pdfs/"):
                s3_key = f"pdfs/{s3_key}"
            
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            print(f"🗑️  Deleted from S3: {s3_key}")
            return True
            
        except Exception as e:
            print(f"❌ Delete failed: {str(e)}")
            return False
    
    def check_bucket_access(self) -> dict:
        """Check if S3 bucket is accessible"""
        try:
            # Try to list objects (limited to 1)
            self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                MaxKeys=1
            )
            
            return {
                "success": True,
                "message": "S3 bucket is accessible",
                "bucket": self.bucket_name,
                "region": self.region
            }
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            return {
                "success": False,
                "error": f"S3 Access Error ({error_code}): {error_message}"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }