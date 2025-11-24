#!/usr/bin/env python3
"""
PruneMate - Docker image & resource cleanup helper, on a schedule!
"""

import os
import sys
import logging
from datetime import datetime

import docker
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from notifications import send_notification

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def get_env_bool(key: str, default: bool = False) -> bool:
    """Get boolean value from environment variable."""
    value = os.environ.get(key, str(default)).lower()
    return value in ('true', '1', 'yes', 'on')


def get_env_str(key: str, default: str = '') -> str:
    """Get string value from environment variable."""
    return os.environ.get(key, default)


def get_env_int(key: str, default: int = 0) -> int:
    """Get integer value from environment variable."""
    try:
        return int(os.environ.get(key, default))
    except ValueError:
        return default


class PruneMate:
    """Docker cleanup manager."""

    def __init__(self):
        """Initialize PruneMate with Docker client and configuration."""
        self.client = docker.from_env()
        
        # Cleanup options
        self.prune_containers = get_env_bool('PRUNE_CONTAINERS', True)
        self.prune_images = get_env_bool('PRUNE_IMAGES', True)
        self.prune_networks = get_env_bool('PRUNE_NETWORKS', True)
        self.prune_volumes = get_env_bool('PRUNE_VOLUMES', False)
        
        # Notification settings
        self.notifications_enabled = get_env_bool('NOTIFICATIONS_ENABLED', False)
        self.notify_only_when_pruned = get_env_bool('NOTIFY_ONLY_WHEN_PRUNED', True)
        self.ntfy_url = get_env_str('NTFY_URL', '')
        self.ntfy_topic = get_env_str('NTFY_TOPIC', '')
        self.ntfy_token = get_env_str('NTFY_TOKEN', '')
        
        logger.info("PruneMate initialized")
        logger.info(f"  Prune containers: {self.prune_containers}")
        logger.info(f"  Prune images: {self.prune_images}")
        logger.info(f"  Prune networks: {self.prune_networks}")
        logger.info(f"  Prune volumes: {self.prune_volumes}")
        logger.info(f"  Notifications enabled: {self.notifications_enabled}")
        if self.notifications_enabled:
            logger.info(f"  Notify only when pruned: {self.notify_only_when_pruned}")

    def prune(self) -> dict:
        """
        Perform Docker cleanup based on configuration.
        
        Returns:
            dict: Results of the cleanup operation
        """
        results = {
            'containers': None,
            'images': None,
            'networks': None,
            'volumes': None,
            'something_pruned': False
        }
        
        logger.info("Starting Docker cleanup...")
        
        if self.prune_containers:
            try:
                result = self.client.containers.prune()
                results['containers'] = result
                deleted_count = len(result.get('ContainersDeleted') or [])
                space_reclaimed = result.get('SpaceReclaimed', 0)
                logger.info(f"Pruned {deleted_count} container(s), reclaimed {self._format_bytes(space_reclaimed)}")
                if deleted_count > 0:
                    results['something_pruned'] = True
            except Exception as e:
                logger.error(f"Error pruning containers: {e}")
        
        if self.prune_images:
            try:
                result = self.client.images.prune(filters={'dangling': False})
                results['images'] = result
                deleted_count = len(result.get('ImagesDeleted') or [])
                space_reclaimed = result.get('SpaceReclaimed', 0)
                logger.info(f"Pruned {deleted_count} image(s), reclaimed {self._format_bytes(space_reclaimed)}")
                if deleted_count > 0:
                    results['something_pruned'] = True
            except Exception as e:
                logger.error(f"Error pruning images: {e}")
        
        if self.prune_networks:
            try:
                result = self.client.networks.prune()
                results['networks'] = result
                deleted_count = len(result.get('NetworksDeleted') or [])
                logger.info(f"Pruned {deleted_count} network(s)")
                if deleted_count > 0:
                    results['something_pruned'] = True
            except Exception as e:
                logger.error(f"Error pruning networks: {e}")
        
        if self.prune_volumes:
            try:
                result = self.client.volumes.prune()
                results['volumes'] = result
                deleted_count = len(result.get('VolumesDeleted') or [])
                space_reclaimed = result.get('SpaceReclaimed', 0)
                logger.info(f"Pruned {deleted_count} volume(s), reclaimed {self._format_bytes(space_reclaimed)}")
                if deleted_count > 0:
                    results['something_pruned'] = True
            except Exception as e:
                logger.error(f"Error pruning volumes: {e}")
        
        logger.info("Docker cleanup completed")
        
        # Send notification if enabled
        if self.notifications_enabled:
            should_notify = not self.notify_only_when_pruned or results['something_pruned']
            if should_notify:
                self._send_notification(results)
            else:
                logger.info("Nothing was pruned, skipping notification")
        
        return results

    def _format_bytes(self, size: int) -> str:
        """Format bytes to human readable string."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"

    def _send_notification(self, results: dict) -> None:
        """Send notification with cleanup results."""
        message = self._build_notification_message(results)
        title = "PruneMate Cleanup Report"
        
        if self.ntfy_url and self.ntfy_topic:
            send_notification(
                provider='ntfy',
                title=title,
                message=message,
                url=self.ntfy_url,
                topic=self.ntfy_topic,
                token=self.ntfy_token
            )
        else:
            logger.warning("Notifications enabled but ntfy is not configured")

    def _build_notification_message(self, results: dict) -> str:
        """Build notification message from results."""
        lines = []
        
        if results['containers'] is not None:
            deleted = results['containers'].get('ContainersDeleted') or []
            space = results['containers'].get('SpaceReclaimed', 0)
            lines.append(f"Containers: {len(deleted)} removed ({self._format_bytes(space)})")
        
        if results['images'] is not None:
            deleted = results['images'].get('ImagesDeleted') or []
            space = results['images'].get('SpaceReclaimed', 0)
            lines.append(f"Images: {len(deleted)} removed ({self._format_bytes(space)})")
        
        if results['networks'] is not None:
            deleted = results['networks'].get('NetworksDeleted') or []
            lines.append(f"Networks: {len(deleted)} removed")
        
        if results['volumes'] is not None:
            deleted = results['volumes'].get('VolumesDeleted') or []
            space = results['volumes'].get('SpaceReclaimed', 0)
            lines.append(f"Volumes: {len(deleted)} removed ({self._format_bytes(space)})")
        
        if not results['something_pruned']:
            lines.append("No resources were pruned.")
        
        return '\n'.join(lines)


def run_scheduled():
    """Run PruneMate as a scheduled job."""
    prunemate = PruneMate()
    prunemate.prune()


def main():
    """Main entry point."""
    # Schedule configuration
    schedule_frequency = get_env_str('SCHEDULE_FREQUENCY', 'daily')
    schedule_time = get_env_str('SCHEDULE_TIME', '03:00')
    schedule_day = get_env_str('SCHEDULE_DAY', '*')
    timezone = get_env_str('TZ', 'Europe/Amsterdam')
    run_on_startup = get_env_bool('RUN_ON_STARTUP', False)
    
    logger.info("PruneMate - Docker image & resource cleanup helper")
    logger.info(f"Schedule: {schedule_frequency} at {schedule_time}")
    if schedule_frequency == 'weekly':
        logger.info(f"Day of week: {schedule_day}")
    logger.info(f"Timezone: {timezone}")
    
    # Parse time
    try:
        hour, minute = schedule_time.split(':')
        hour = int(hour)
        minute = int(minute)
    except ValueError:
        logger.error(f"Invalid SCHEDULE_TIME format: {schedule_time}. Use HH:MM format.")
        sys.exit(1)
    
    # Create scheduler
    tz = pytz.timezone(timezone)
    scheduler = BlockingScheduler(timezone=tz)
    
    # Configure trigger based on frequency
    if schedule_frequency == 'hourly':
        trigger = CronTrigger(minute=minute, timezone=tz)
    elif schedule_frequency == 'daily':
        trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)
    elif schedule_frequency == 'weekly':
        trigger = CronTrigger(day_of_week=schedule_day, hour=hour, minute=minute, timezone=tz)
    elif schedule_frequency == 'monthly':
        trigger = CronTrigger(day=1, hour=hour, minute=minute, timezone=tz)
    else:
        logger.error(f"Invalid SCHEDULE_FREQUENCY: {schedule_frequency}")
        sys.exit(1)
    
    scheduler.add_job(run_scheduled, trigger, id='prune_job', name='Docker Prune Job')
    
    # Run immediately on startup if configured
    if run_on_startup:
        logger.info("Running cleanup on startup...")
        run_scheduled()
    
    logger.info("Scheduler started. Waiting for next scheduled run...")
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


if __name__ == '__main__':
    main()
