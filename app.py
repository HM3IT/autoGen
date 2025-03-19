import json
import os
from dotenv import load_dotenv
from typing import Any, Awaitable, Callable, Optional
 
import aiofiles
import yaml
from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_agentchat.base import TaskResult
from autogen_agentchat.messages import TextMessage, UserInputRequestedEvent

from litestar import Litestar, get, post
from litestar.config.cors import CORSConfig
from litestar.exceptions import HTTPException
from litestar.logging import LoggingConfig
from litestar import Litestar, WebSocket, websocket_listener
from litestar.exceptions import WebSocketDisconnect
from autogen_agentchat.teams import RoundRobinGroupChat

load_dotenv()

model = os.environ["MODEL_NAME"]
api_key = os.environ["API_KEY"]
base_url = os.environ["LLM_BASE_URL"]

cors_config = CORSConfig(allow_origins=["*"], allow_credentials=False, allow_headers=["*"], )

model_config_path = "model_config.yaml"
state_path = "agent_state.json"
history_path = "agent_history.json"

logging_config = LoggingConfig(
    root={"level": "INFO", "handlers": ["queue_listener"]},
    formatters={
        "standard": {"format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"}
    },
    log_exceptions="always",
)

model_config = {
    "model": model,
    "api_key":api_key,
    "base_url":base_url,
    "model_info":{
        "family": "llama",
        "vision": False,
        "json_output": True,
        "function_calling": True,
    },
}

logger = logging_config.configure()()

@get("/")
async def index() -> str:
    return "Hello, world!"

async def get_agent() -> AssistantAgent:
    """Get the assistant agent, load state from file."""
 
    model_client = OpenAIChatCompletionClient(**model_config)
 
    agent = AssistantAgent(
        name="assistant",
        model_client=model_client,
        system_message="You are a helpful assistant.",
    )
 
    if not os.path.exists(state_path):
        return agent  # Return agent without loading state.
    async with aiofiles.open(state_path, "r") as file:
        state = json.loads(await file.read())
    await agent.load_state(state)
    logger.info("Agent")
    logger.info(agent)
    return agent



async def get_history() -> list[dict[str, Any]]:
    """Get chat history from file."""
    if not os.path.exists(history_path):
        return []
    async with aiofiles.open(history_path, "r") as file:
        return json.loads(await file.read())


@get("/history")
async def history() -> list[dict[str, Any]]:
    try:
        return await get_history()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@post("/chat")
async def chat_agent(data: str) -> TextMessage:
    try:
 
        agent = await get_agent()
        input_text = TextMessage(content=data, source="user")
        response = await agent.on_messages(messages=[input_text], cancellation_token=CancellationToken())

 
        state = await agent.save_state()
        async with aiofiles.open(state_path, "w") as file:
            await file.write(json.dumps(state))
 
        history = await get_history()
        history.append(input_text.model_dump())
        history.append(response.chat_message.model_dump())
        async with aiofiles.open(history_path, "w") as file:
            await file.write(json.dumps(history))

        assert isinstance(response.chat_message, TextMessage)
        return response.chat_message
    except Exception as e:
        error_message = {
            "type": "error",
            "content": f"Error: {str(e)}",
            "source": "system"
        }
        raise HTTPException(status_code=500, detail=error_message) from e


class ChatWebSocket(WebSocket):
    
    async def get_team(
    user_input_func: Callable[[str, Optional[CancellationToken]], Awaitable[str]],
) -> RoundRobinGroupChat:
 
        model_client = OpenAIChatCompletionClient(**model_config)
 
        # Create the team.
        agent = AssistantAgent(
            name="assistant",
            model_client=model_client,
            system_message="You are a helpful assistant.",
        )
        yoda = AssistantAgent(
            name="yoda",
            model_client=model_client,
            system_message="Repeat the same message in the tone of Yoda.",
        ) 
        user_proxy = UserProxyAgent(
            name="user",
            input_func=user_input_func,  # Use the user input function.
        )
        team = RoundRobinGroupChat(
            [agent, yoda, user_proxy],
        )
        # Load state from file.
        if not os.path.exists(state_path):
            return team
        async with aiofiles.open(state_path, "r") as file:
            state = json.loads(await file.read())
        await team.load_state(state)
        return team

    async def on_accept(self) -> None:
        """Handle new WebSocket connection."""
        await super().on_accept()
        # Additional setup if needed

    async def on_receive(self, data: str) -> None:
        """Handle incoming messages from the client."""
        try:
            request = TextMessage.model_validate(json.loads(data))
            history = await get_history()
            team = await self.get_team(self._user_input)

            stream = team.run_stream(task=request)
            async for message in stream:
                if isinstance(message, TaskResult):
                    continue
                await self.send_json(message.model_dump())
                if not isinstance(message, UserInputRequestedEvent):
                    history.append(message.model_dump())

            # Save team state to file
            async with aiofiles.open(state_path, "w") as file:
                state = await team.save_state()
                await file.write(json.dumps(state))

            # Save chat history to file
            async with aiofiles.open(history_path, "w") as file:
                await file.write(json.dumps(history))

        except Exception as e:
            error_message = {
                "type": "error",
                "content": f"Error: {str(e)}",
                "source": "system"
            }
            await self.send_json(error_message)
            # Re-enable input after error
            await self.send_json({
                "type": "UserInputRequestedEvent",
                "content": "An error occurred. Please try again.",
                "source": "system"
            })

    async def on_disconnect(self) -> None:
        """Handle WebSocket disconnection."""
        await super().on_disconnect()
        # Additional teardown if needed

    async def _user_input(self, prompt: str, cancellation_token: Optional[CancellationToken] = None) -> str:
        """Handle user input function used by the team."""
        data = await self.receive_json()
        message = TextMessage.model_validate(data)
        return message.content



app = Litestar([index, chat_agent, history, ChatWebSocket], cors_config=cors_config,    logging_config=logging_config,)