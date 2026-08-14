/**
 * PostgreSQL (with pgvector) and Redis.
 *
 * Both live in the isolated subnets with no route to the internet. Neither is
 * reachable from anywhere except the ECS task security group.
 */

# ---------------------------------------------------------------------------
# Security groups
# ---------------------------------------------------------------------------

resource "aws_security_group" "database" {
  name        = "${local.name}-database"
  description = "PostgreSQL — reachable only from Clinchec services"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-database" }
}

resource "aws_security_group" "cache" {
  name        = "${local.name}-cache"
  description = "ElastiCache Redis — reachable only from Clinchec services"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-cache" }
}

resource "aws_vpc_security_group_ingress_rule" "database_from_services" {
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = aws_security_group.service.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "PostgreSQL from ECS tasks"
}

resource "aws_vpc_security_group_ingress_rule" "cache_from_services" {
  security_group_id            = aws_security_group.cache.id
  referenced_security_group_id = aws_security_group.service.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
  description                  = "Redis from ECS tasks"
}

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

resource "random_password" "database" {
  length  = 40
  special = true
  # RDS rejects these in a master password.
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_secretsmanager_secret" "database_url" {
  name       = "clinchec/${var.environment}/database_url"
  kms_key_id = aws_kms_key.main.arn
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id = aws_secretsmanager_secret.database_url.id
  secret_string = format(
    "postgresql+asyncpg://%s:%s@%s:%s/%s",
    aws_db_instance.main.username,
    random_password.database.result,
    aws_db_instance.main.address,
    aws_db_instance.main.port,
    aws_db_instance.main.db_name,
  )
}

resource "aws_secretsmanager_secret" "redis_url" {
  name       = "clinchec/${var.environment}/redis_url"
  kms_key_id = aws_kms_key.main.arn
}

resource "aws_secretsmanager_secret_version" "redis_url" {
  secret_id = aws_secretsmanager_secret.redis_url.id
  secret_string = format(
    "rediss://%s:6379/0",
    aws_elasticache_replication_group.main.primary_endpoint_address,
  )
}

# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

resource "aws_db_subnet_group" "main" {
  name       = "${local.name}-db"
  subnet_ids = aws_subnet.isolated[*].id
}

resource "aws_db_parameter_group" "main" {
  name   = "${local.name}-pg16"
  family = "postgres16"

  # Force TLS for every client connection.
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  # Log any statement over a second — the payer-rule similarity searches are
  # the ones worth watching as the corpus grows.
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }

  parameter {
    name         = "shared_preload_libraries"
    value        = "pg_stat_statements"
    apply_method = "pending-reboot"
  }
}

resource "aws_db_instance" "main" {
  identifier     = "${local.name}-postgres"
  engine         = "postgres"
  engine_version = "16.4"

  instance_class    = var.environment == "production" ? "db.r6g.xlarge" : "db.t4g.medium"
  allocated_storage = var.environment == "production" ? 200 : 40
  # Headroom so a rules-crawl burst cannot fill the volume.
  max_allocated_storage = var.environment == "production" ? 2000 : 200
  storage_type          = "gp3"

  db_name  = "clinchec"
  username = "clinchec_app"
  password = random_password.database.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.database.id]
  parameter_group_name   = aws_db_parameter_group.main.name
  publicly_accessible    = false

  storage_encrypted = true
  kms_key_id        = aws_kms_key.main.arn

  multi_az                = var.environment == "production"
  backup_retention_period = var.environment == "production" ? 35 : 7
  backup_window           = "07:00-08:00"
  maintenance_window      = "Mon:08:30-Mon:09:30"
  copy_tags_to_snapshot   = true

  # Deleting a PHI database must be a deliberate two-step act.
  deletion_protection       = var.environment == "production"
  skip_final_snapshot       = var.environment != "production"
  final_snapshot_identifier = "${local.name}-final-${formatdate("YYYYMMDDhhmmss", timestamp())}"

  performance_insights_enabled          = true
  performance_insights_kms_key_id       = aws_kms_key.main.arn
  performance_insights_retention_period = var.environment == "production" ? 731 : 7

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  auto_minor_version_upgrade      = true

  lifecycle {
    # `timestamp()` in the snapshot name would otherwise force replacement on
    # every plan.
    ignore_changes = [final_snapshot_identifier]
  }
}

# ---------------------------------------------------------------------------
# Redis — Celery broker and result backend
# ---------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "main" {
  name       = "${local.name}-cache"
  subnet_ids = aws_subnet.isolated[*].id
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "${local.name}-redis"
  description          = "Clinchec Live task broker"

  engine         = "redis"
  engine_version = "7.1"
  node_type      = var.environment == "production" ? "cache.r7g.large" : "cache.t4g.micro"
  port           = 6379

  num_cache_clusters         = var.environment == "production" ? 2 : 1
  automatic_failover_enabled = var.environment == "production"
  multi_az_enabled           = var.environment == "production"

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.cache.id]

  at_rest_encryption_enabled = true
  kms_key_id                 = aws_kms_key.main.arn
  transit_encryption_enabled = true

  snapshot_retention_limit = var.environment == "production" ? 7 : 1
  maintenance_window       = "tue:09:00-tue:10:00"
  apply_immediately        = var.environment != "production"
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "database_endpoint" {
  value     = aws_db_instance.main.endpoint
  sensitive = true
}

output "redis_endpoint" {
  value     = aws_elasticache_replication_group.main.primary_endpoint_address
  sensitive = true
}

output "database_secret_arn" {
  value = aws_secretsmanager_secret.database_url.arn
}
