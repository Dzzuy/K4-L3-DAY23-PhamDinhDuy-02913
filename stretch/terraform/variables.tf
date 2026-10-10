variable "source_region" {
  description = "AWS Region holding the primary snapshot bucket."
  type        = string
}

variable "destination_region" {
  description = "AWS Region holding the DR snapshot bucket."
  type        = string
}

variable "source_bucket_name" {
  description = "Globally unique bucket name for primary snapshots."
  type        = string
}

variable "destination_bucket_name" {
  description = "Globally unique bucket name for DR snapshots."
  type        = string
}
