import pulumi
import pulumi_aws as aws
import os
import mimetypes
import json

config = pulumi.Config()
site_dir = config.require("siteDir")

# --- 1. Create the Bucket ---
# Create the Bucket
bucket = aws.s3.Bucket("my-bucket")

# NEW RESOURCE: Explicitly disable all public access blocking for this bucket
# This allows the subsequent BucketPolicy to be applied.
public_access_block = aws.s3.BucketPublicAccessBlock("public-access-block",
    bucket=bucket.id,
    block_public_acls=False,
    block_public_policy=False,  # <--- This is the key setting that was blocking your policy!
    ignore_public_acls=False,
    restrict_public_buckets=False,
)

# --- 2. Configure Website Hosting (FIXED) ---
# The index_document argument must be an object { "suffix": "..." }
website_config = aws.s3.BucketWebsiteConfiguration("my-bucket-website",
    bucket=bucket.id,
    index_document={
        "suffix": "index.html",
    },
)

# --- 3. Define the Public Read Policy ---
public_read_policy = aws.s3.BucketPolicy("public-read-policy",
    bucket=bucket.id,
    policy=bucket.arn.apply(lambda arn: json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": "*",
            "Action": [
                "s3:GetObject"
            ],
            "Resource": [
                f"{arn}/*",
            ]
        }]
    }))
)

for file in os.listdir(site_dir):
    # --- 4. Upload the Index File ---
    filepath = os.path.join(site_dir, file)
    mime_type, _ = mimetypes.guess_type(filepath)

    obj = aws.s3.BucketObject(file,
        bucket=bucket.bucket,
        source=pulumi.FileAsset(filepath),
        content_type=mime_type,
    )

# --- Exports ---
pulumi.export('bucket_name', bucket.bucket)
pulumi.export('bucket_endpoint', website_config.website_endpoint)