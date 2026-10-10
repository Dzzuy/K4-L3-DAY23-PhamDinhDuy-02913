# Write-only lab draft: do not apply without real AWS credentials, names and review.
# state/snapshot.py put writes vectors.sqlite, model.bin and MANIFEST.json;
# replication copies every version of those objects to the DR Region.
resource "aws_s3_bucket" "source" {
  provider = aws.source
  bucket   = var.source_bucket_name
}

resource "aws_s3_bucket" "destination" {
  provider = aws.destination
  bucket   = var.destination_bucket_name
}

resource "aws_s3_bucket_versioning" "source" {
  provider = aws.source
  bucket   = aws_s3_bucket.source.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_versioning" "destination" {
  provider = aws.destination
  bucket   = aws_s3_bucket.destination.id
  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_iam_policy_document" "replication_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "replication" {
  provider           = aws.source
  name_prefix        = "day23-dr-replication-"
  assume_role_policy = data.aws_iam_policy_document.replication_assume.json
}

data "aws_iam_policy_document" "replication" {
  statement {
    actions   = ["s3:GetReplicationConfiguration", "s3:ListBucket"]
    resources = [aws_s3_bucket.source.arn]
  }

  statement {
    actions = [
      "s3:GetObjectVersionForReplication",
      "s3:GetObjectVersionAcl",
      "s3:GetObjectVersionTagging",
    ]
    resources = ["${aws_s3_bucket.source.arn}/*"]
  }

  statement {
    actions   = ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"]
    resources = ["${aws_s3_bucket.destination.arn}/*"]
  }
}

resource "aws_iam_role_policy" "replication" {
  provider = aws.source
  role     = aws_iam_role.replication.id
  policy   = data.aws_iam_policy_document.replication.json
}

resource "aws_s3_bucket_replication_configuration" "snapshots" {
  provider = aws.source
  bucket   = aws_s3_bucket.source.id
  role     = aws_iam_role.replication.arn

  depends_on = [
    aws_s3_bucket_versioning.source,
    aws_s3_bucket_versioning.destination,
    aws_iam_role_policy.replication,
  ]

  rule {
    id     = "all-snapshot-objects"
    status = "Enabled"
    filter {}

    destination {
      bucket        = aws_s3_bucket.destination.arn
      storage_class = "STANDARD"
    }
  }
}
