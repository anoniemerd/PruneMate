#!/usr/bin/env python3
"""
Notification providers for PruneMate.
"""

import logging
import requests

logger = logging.getLogger(__name__)


def send_notification(
    provider: str,
    title: str,
    message: str,
    **kwargs
) -> bool:
    """
    Send notification using the specified provider.
    
    Args:
        provider: Notification provider name ('ntfy')
        title: Notification title
        message: Notification message
        **kwargs: Provider-specific arguments
        
    Returns:
        bool: True if notification was sent successfully
    """
    if provider == 'ntfy':
        return send_ntfy_notification(
            title=title,
            message=message,
            url=kwargs.get('url', ''),
            topic=kwargs.get('topic', ''),
            token=kwargs.get('token', '')
        )
    else:
        logger.error(f"Unknown notification provider: {provider}")
        return False


def send_ntfy_notification(
    title: str,
    message: str,
    url: str,
    topic: str,
    token: str = ''
) -> bool:
    """
    Send notification via ntfy.
    
    Args:
        title: Notification title
        message: Notification message
        url: ntfy server URL (e.g., https://ntfy.sh)
        topic: ntfy topic name
        token: Optional authentication token
        
    Returns:
        bool: True if notification was sent successfully
    """
    if not url or not topic:
        logger.error("ntfy URL and topic are required")
        return False
    
    # Construct the full URL
    ntfy_url = f"{url.rstrip('/')}/{topic}"
    
    headers = {
        'Title': title,
        'Priority': 'default'
    }
    
    # Add authentication if token is provided
    if token:
        headers['Authorization'] = f'Bearer {token}'
    
    try:
        response = requests.post(
            ntfy_url,
            data=message.encode('utf-8'),
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        logger.info(f"Notification sent successfully to {ntfy_url}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send ntfy notification: {e}")
        return False
