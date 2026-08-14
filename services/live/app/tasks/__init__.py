"""Celery task package for Clinchec Live."""

from app.tasks.celery_app import celery_app

__all__ = ["celery_app"]
