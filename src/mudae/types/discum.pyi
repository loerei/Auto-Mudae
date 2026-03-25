"""
Type stub file for discum library.
Provides type hints for commonly used discum methods to help with IDE autocompletion
and type checking in Pylance.
"""

from typing import Any, Dict, List, Optional

class Client:
    """Discord bot client for interacting with Discord API"""
    
    def __init__(self, token: str, log: bool = False) -> None:
        """Initialize Discord client
        
        Args:
            token: Discord bot token
            log: Whether to enable logging
        """
        ...
    
    def getSlashCommands(self, botID: str) -> "SlashCommandsResponse":
        """Get slash commands for a bot
        
        Args:
            botID: The bot's Discord ID
            
        Returns:
            SlashCommandsResponse object with .json() method
        """
        ...
    
    def triggerSlashCommand(
        self, 
        botID: str, 
        channelID: str, 
        guildID: str, 
        data: Dict[str, Any]
    ) -> Any:
        """Trigger a slash command
        
        Args:
            botID: The bot's Discord ID
            channelID: The channel ID
            guildID: The guild/server ID
            data: Command data structure
            
        Returns:
            Response from Discord API
        """
        ...
    
    def click(
        self,
        authorID: str,
        *,
        channelID: str,
        guildID: str,
        messageID: str,
        messageFlags: int,
        data: Dict[str, Any]
    ) -> Any:
        """Click/interact with a message component
        
        Args:
            authorID: Author's Discord ID
            channelID: The channel ID
            guildID: The guild/server ID
            messageID: The message ID
            messageFlags: Message flags
            data: Component interaction data
            
        Returns:
            Response from Discord API
        """
        ...
    
    def sendMessage(self, channelID: str, content: str) -> Any:
        """Send a message to a channel
        
        Args:
            channelID: The channel ID
            content: Message content
            
        Returns:
            Response from Discord API
        """
        ...

class SlashCommandsResponse:
    """Response object from getSlashCommands"""
    
    def json(self) -> List[Dict[str, Any]]:
        """Get JSON representation of slash commands
        
        Returns:
            List of command dictionaries
        """
        ...

class SlashCommander:
    """Helper class for building and executing slash commands"""
    
    def __init__(self, commands: List[Dict[str, Any]]) -> None:
        """Initialize SlashCommander with command list
        
        Args:
            commands: List of command dictionaries from Client.getSlashCommands().json()
        """
        ...
    
    def get(self, cmdList: List[str], inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Get a specific command by name
        
        Args:
            cmdList: List of command names to find (e.g., ['tu'] or ['rollsutil', 'resetclaimtimer'])
            inputs: Optional command inputs
            
        Returns:
            Command data structure ready to pass to triggerSlashCommand
        """
        ...
