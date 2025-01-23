import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, List, Any
from uuid import UUID, uuid4
import threading
import uvicorn
import time
import json
from datetime import datetime
from dataclasses import asdict

from agentslack.types import Message, Channel, Agent
from agentslack.registry import Registry




class Tool(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)

class WorldRegistration(BaseModel):
    world_name: str

class AgentRegistration(BaseModel):
    agent_name: str
    world_name: str

class HistoryExport(BaseModel):
    world_name: str
    limit: int

class AgentLogsExport(BaseModel):
    agent_name: str

class Server:
    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.app = FastAPI()
        self.host = host
        self.port = port
        self.registry = Registry()
        self.tools = {
            "send_direct_message": Tool(
                name="send_direct_message",
                description="Send a message to a user",
                parameters={
                    "your_name": "string",
                    "recipient_name": "string",
                    "message": "string",
                }
            ),
            "send_message_to_channel": Tool(
                name="send_message_to_channel",
                description="Send a message to a channel",
                parameters={
                    "your_name": "string",
                    "channel_name": "string",
                    "message": "string",
                }
            ),
            "read_dm": Tool(
                name="read_dm",
                description="Read a direct message",
                parameters={
                    "your_name": "string",
                    "sender_name": "string",
                }
            ),
            "check_new_messages": Tool(
                name="check_new_messages",
                description="Check if there are new messages across all channels and dms",
                parameters={
                    "your_name": "string"
                }
            ),
            "read_channel": Tool(
                name="read_channel",
                description="Read a channel",
                parameters={
                    "your_name": "string",
                    "channel_name": "string",
                }
            ),
            "list_channels": Tool(
                name="list_all_my_channels",
                description="List all channels I have access to",
                parameters={
                    "agent_name": "string"
                }
            ),
            "create_channel": Tool(
                name="create_channel",
                description="Create a new channel",
                parameters={
                    "your_name": "string",
                    "channel_name": "string",
                }
            ),
            "get_human_info": Tool(
                name="get_human_info",
                description="Get information about available humans to consult.",
                parameters={
                    "your_name": "string"
                }
            ),
            "send_message_to_human": Tool(
                name="send_message_to_human",
                description="Send a message to a human",
                parameters={
                    "your_name": "string",
                    "human_name": "string",
                    "message": "string"
                }
            ),
            "add_member_to_channel": Tool(
                name="add_member_to_channel",
                description="Add a member to a channel",
                parameters={
                    "your_name": "string",
                    "member_to_add": "string",
                    "channel_name": "string"
                }
            )
        }
        self.server_thread = None
        self._setup_routes()

    def _setup_routes(self):
        @self.app.get("/tools")
        async def list_tools():
            return list(self.tools.values())
        
        @self.app.post("/tools/{tool_name}")
        async def call_tool(tool_name: str, parameters: Dict[str, Any]):
            if tool_name not in self.tools:
                raise HTTPException(status_code=404, detail="Tool not found")
                
            if tool_name == "send_direct_message":
                if not self.agent_exists(parameters["recipient_name"]):
                    if self.human_exists(parameters["recipient_name"]):
                        return f"You are trying to send a message to a human. For that use the send_message_to_human tool."
                    else:
                        return self.return_agent_doesnt_exist_error(parameters["recipient_name"])
                if not self.agent_exists(parameters["your_name"]):
                    return self.return_agent_doesnt_exist_error(parameters["your_name"])
                
                slack_client = self.registry.get_agent(parameters["your_name"]).slack_client
                id_of_recipient = self.registry.get_agent(parameters["recipient_name"]).slack_app.slack_id
                
                response = slack_client.open_conversation(user_ids=[id_of_recipient])
                if response['ok']:
                    channel_id = response['channel']['id']
                else:
                    raise HTTPException(status_code=400, detail="Failed to open conversation")

                response = slack_client.send_messsage(
                    message=parameters["message"],
                    target_channel_id=channel_id
                )
                self.update_channels(parameters["your_name"])
                # update the agent's channel with this message
                self._update_agent_read_messages(parameters["your_name"], channel_id, [Message(message=parameters["message"], channel_id=channel_id, user_id=self.registry.get_agent(parameters["your_name"]).slack_app.slack_id, timestamp=time.time(), agent_name=parameters["your_name"])])
                return response
            
            elif tool_name == "send_message_to_channel":
                # send message to a channel 
                if not self.agent_exists(parameters["your_name"]):
                    return self.return_agent_doesnt_exist_error(parameters["your_name"])
                slack_client = self.registry.get_agent(parameters["your_name"]).slack_client
                channel_name = parameters["channel_name"]
                channels = self.registry.get_agent(parameters["your_name"]).channels
                channel_names = [channel.name for channel in channels]
                if channel_name not in channel_names:
                    response = "Sorry this channel doesn't exist, you can create a new channel with the create_channel tool."
                    response += "Here is a list of all the channels you have access to: " + str(channel_names)
                else: 
                    channel_id = self.registry.get_channel(channel_name).slack_id
                    response = slack_client.send_messsage(
                        message=parameters["message"],
                        target_channel_id=channel_id
                    )
                    # update the agent's channel with this message
                    self._update_agent_read_messages(parameters["your_name"], channel_id, [Message(message=parameters["message"], channel_id=channel_id, user_id=parameters["your_name"], timestamp=time.time(), agent_name=parameters["your_name"])])
                    self.update_channels(parameters["your_name"])
                return response

            elif tool_name == "list_channels":
                if not self.agent_exists(parameters["your_name"]):
                    return self.return_agent_doesnt_exist_error(parameters["your_name"])
                slack_client = self.registry.get_agent(parameters["your_name"]).slack_client    
                response = slack_client.list_channels()
                return response['channels']
            
            elif tool_name == "read_channel":
                if not self.agent_exists(parameters["your_name"]):
                    return self.return_agent_doesnt_exist_error(parameters["your_name"])
                slack_client = self.registry.get_agent(parameters["your_name"]).slack_client
                channel_id = self.registry.get_channel(parameters["channel_name"]).slack_id

                # TODO add error if the channel doesn't exist
                response = slack_client.read(channel_id=channel_id)
                
                # TODO: this can also be because there are no messages in the channel.
                if len(response['messages']) == 0:
                    return "You are not a member of this channel, you can't read it."
                
                agent = self.registry.get_agent(parameters["your_name"])
                world_start_datetime = self.registry.get_world(agent.world_name).start_datetime
                # restrict to messages after the world start datetime 
                messages = response['messages']
                messages = [msg for msg in messages if datetime.fromtimestamp(float(msg['ts'])).timestamp() > world_start_datetime]
                for message in messages:
                    if message['user'] in self.registry.get_all_agent_names():
                        message['agent_name'] = self.registry.get_agent_name_from_id(message['user'])
                    else:
                        message['agent_name'] = self.registry.get_human_name_from_id(message['user'])
                messages = [
                    Message(
                        message=message['text'], 
                        channel_id=channel_id, 
                        user_id=message['user'], 
                        timestamp=message['ts'], 
                        agent_name=message['agent_name']    
                    ) for message in messages]
                # update the agent's channel with these messages
                self._update_agent_read_messages(parameters["your_name"], channel_id, messages)
                return messages
            
            elif tool_name == "read_dm":
                if not self.agent_exists(parameters["your_name"]):
                    return self.return_agent_doesnt_exist_error(parameters["your_name"])
                if not self.agent_exists(parameters["sender_name"]):
                    return self.return_agent_doesnt_exist_error(parameters["sender_name"], sender=True)
                
                # NOTE: DMs for now are only between two agents (plus humans), i.e., we don't allow for mpim
                total_users = len(self.registry.get_humans()) + 2
                your_agent = self.registry.get_agent(parameters["your_name"])
                
                # get the main agent 
                slack_client = your_agent.slack_client
                sender_name = parameters["sender_name"]

                world_start_datetime = self.registry.get_world_starttime_of_agent(sender_name)

                # get the ids, needed for communication with slack 
                sender_agent = self.registry.get_agent(sender_name)
                sender_id = sender_agent.slack_app.slack_id
                receiver_id = your_agent.slack_app.slack_id
                # loop over channels from the agent 
                channels = slack_client.check_ongoing_dms()
                
                # TODO: get a better way of keeping track of dm channels, to not overload the api as is currently done. 
                for channel in channels['channels']:
                    members = slack_client.get_channel_members(channel['id'])['members']
                    if len(members) == total_users:
                        # make sure both the sender and receiver are in the channel 
                        if (sender_id in members) and (receiver_id in members):
                            channel_id = channel['id']
                            break

                response = slack_client.read(channel_id=channel_id)
                if response.get('error'):
                    return response.get('error')
                messages = []
                for message in response['messages']:
                    if message['user'] in self.registry.get_all_agent_names():
                        message['agent_name'] = self.registry.get_agent_name_from_id(message['user'])
                    else:
                        message['agent_name'] = self.registry.get_human_name_from_id(message['user'])
                    if datetime.fromtimestamp(float(message['ts'])).timestamp() >= world_start_datetime:
                        messages.append(Message(
                            message=message['text'], 
                            channel_id=channel_id, 
                            user_id=message['user'], 
                            timestamp=message['ts'].split('.')[0], 
                            agent_name=message['agent_name']
                            )
                        )
                self._update_agent_read_messages(parameters["your_name"], channel_id, messages)
                return messages
            
            elif tool_name == "check_ongoing_dms":
                if not self.agent_exists(parameters["your_name"]):
                    return self.return_agent_doesnt_exist_error(parameters["your_name"])
                response = self.registry.get_agent(parameters["your_name"]).slack_client.check_ongoing_dms()
                return response
            
            elif tool_name == "check_new_messages":
                # This should be the main endpoint for the agent to check for new messages
                # return all new messages channels and dms the user is a part of 
                # ensure the timestamp of the messages is greater than the start of the world 
                if not self.agent_exists(parameters["your_name"]):
                    return self.return_agent_doesnt_exist_error(parameters["your_name"])
                
                # get agent information 
                agent = self.registry.get_agent(parameters["your_name"])
                agent_id = agent.slack_app.slack_id

                # get world information     
                world_start_datetime = self.registry.get_world(agent.world_name).start_datetime

                # update the channels the agent is a part of NOTE this might be redundant 
                self.update_channels(parameters["your_name"])
                channels = agent.channels
                channel_ids = [channel.slack_id for channel in channels]

                channel_ids_with_agent = []
                # get_channel_members and check for agent in members 
                for channel_id in channel_ids:
                    members = agent.slack_client.get_channel_members(channel_id)['members']
                    if agent_id in members:
                        channel_ids_with_agent.append(channel_id)

                # get all new messages in the channels the agent is a part of 
                all_new_messages = []
                for channel_id in channel_ids_with_agent:
                    messages = agent.slack_client.read(channel_id)['messages']

                    # filter to make sure the messages are after the world start datetime
                    msgs_after = [msg for msg in messages if datetime.fromtimestamp(float(msg['ts'])).timestamp() >= world_start_datetime]

                    # convert to Message objects 
                    msgs_after = [Message(
                        message=message['text'], 
                        channel_id=channel_id, 
                        user_id=message['user'], 
                        timestamp=message['ts'].split('.')[0], 
                        agent_name=self.registry.get_agent_name_from_id(message['user'])) 
                        for message in msgs_after]
                    
                    if len(msgs_after) == 0:
                        continue
    
                    # filter out messages that the agent has already seen
                    new_messages = self.only_show_new_messages(parameters["your_name"], channel_id, msgs_after)
                    all_new_messages.append(new_messages)
                    # update the agent's channel with these new messages
                    self._update_agent_read_messages(parameters["your_name"], channel_id, new_messages)
                return all_new_messages
            
            elif tool_name == "get_human_info":
                # get's the metadata about the human in the world 
                humans = self.registry.get_humans()
                return humans
            
            elif tool_name == "send_message_to_human":
                slack_client = self.registry.get_agent(parameters["your_name"]).slack_client
                # get the human id 
                
                human_id = self.registry.get_human(parameters["human_name"]).slack_member_id

                if parameters["human_name"] not in self.registry.get_human_names():
                    return f"The human '{parameters['human_name']}' does not exist, here are possible humans: {self.registry.get_human_names()}"

                response = slack_client.open_conversation(user_ids=[human_id])
                if response['ok']:
                    channel_id = response['channel']['id']
                else:
                    raise HTTPException(status_code=400, detail="Failed to open conversation")

                response = slack_client.send_messsage(
                    message=parameters["message"],
                    target_channel_id=channel_id
                )
                # update the agent's channel with this message
                self._update_agent_read_messages(
                    parameters["your_name"], 
                    channel_id, 
                    [Message(
                        message=parameters["message"], 
                        channel_id=channel_id, 
                        user_id=self.registry.get_agent(parameters["your_name"]).slack_app.slack_id, 
                        timestamp=time.time(), 
                        agent_name=parameters["your_name"])])
                return response
            
            elif tool_name == "create_channel":
                parameters['channel_name'] = parameters['channel_name'].lower()
                
                if parameters["your_name"] not in self.registry.get_all_agent_names():
                    return f"Your name is incorrect, here are possible variants for your name: {self.registry.get_all_agent_names()}"
                
                slack_client = self.registry.get_agent(parameters["your_name"]).slack_client
                response = slack_client.create_channel(
                    channel_name=parameters["channel_name"],
                )
                
                self.registry.register_channel(
                    agent_name=parameters["your_name"], 
                    channel_name=parameters["channel_name"], 
                    channel_id=response['channel']['id']
                )
                return response
            
            elif tool_name == "add_member_to_channel":
                agent = self.registry.get_agent(parameters["your_name"])
                
                if parameters["member_to_add"] not in self.registry.get_all_agent_names():
                    if parameters["member_to_add"] in self.registry.get_human_names():
                        return f"You are trying to add a human to a channel. You can't add humans to a channel directly. Ask the human directly to join."
                    else:
                        return f"The member '{parameters['member_to_add']}' does not exist, here are the names of all agents: {self.registry.get_all_agent_names()}"
                else:
                    other_agent = self.registry.get_agent(parameters["member_to_add"])
                
                
                channel = self.registry.get_channel(parameters["channel_name"])
                
                response = agent.slack_client.add_user_to_channel(
                    channel_id=channel.slack_id,
                    user_id=[other_agent.slack_app.slack_id]
                )
                return response

            raise HTTPException(status_code=400, detail="Tool execution failed")

        @self.app.post("/register_world")
        async def register_world(request: WorldRegistration) -> str:
            try:
                self.registry.register_world(request.world_name)
                return f"World {request.world_name} registered successfully"
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))

        @self.app.post("/register_agent")
        async def register_agent(request: AgentRegistration) -> str:
            try:
                self.registry.register_agent(request.agent_name, request.world_name)
                return f"Agent {request.agent_name} registered successfully in world {request.world_name}"
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))
            
            
        @self.app.get("/export_history")
        async def export_history(request: HistoryExport) -> list[dict]:
            try:
                client = self.registry.get_world(request.world_name).slack_client
                channels = self.registry.get_world(request.world_name).channels
                channel_names = [channel.name for channel in channels]
                
                time.sleep(5)
        
                response = client.export_history(channel_names=channel_names, limit=request.limit)
                
                world_start_datetime = self.registry.get_world(request.world_name).start_datetime
                print(f"[DEBUG] World start datetime: {world_start_datetime}")
                messages_to_return = []
                for channel_id, channel_data in response.items():
                    for message in channel_data['messages']:
                        if datetime.fromtimestamp(float(message['ts'])).timestamp() >= world_start_datetime:
                            messages_to_return.append(message)
                
                return messages_to_return
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))
        
        @self.app.get("/export_agent_logs")
        async def export_agent_logs(request: AgentLogsExport) -> list[dict]:
            try:
                agent = self.registry.get_agent(request.agent_name)
                self._export_agent_logs(agent)
                return f"Agent {request.agent_name} logs exported successfully"
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))

    def agent_exists(self, agent_name: str) -> bool:
        return self.registry.agent_exists(agent_name)
    
    def human_exists(self, human_name: str) -> bool:
        return self.registry.human_exists(human_name)

    def return_agent_doesnt_exist_error(self, agent_name: str, sender: bool = False) -> str:
        if sender:
            return f"The sender '{agent_name}' does not exist, here are possible agents: {self.registry.get_all_agent_names()}"
        else:
            return f"The agent '{agent_name}' does not exist, here are possible agents: {self.registry.get_all_agent_names()}"
    
    def return_human_doesnt_exist_error(self, human_name: str) -> str:
        return f"The human '{human_name}' does not exist, here are possible humans: {self.registry.get_human_names()}"

    def only_show_new_messages(self, agent_name: str, channel_id: str, messages: List[Message]) -> List[Message]:
        # filter based on messages the agent has already seen
        # take in a new list of messages from a channel
        # filter the messages that were not in the previous agent set of messages
        # return the new messages
        agent = self.registry.get_agent(agent_name)
        
        previous_messages = agent.read_messages.get(channel_id, [])
        
        new_messages = [msg for msg in messages if msg not in previous_messages]
        return new_messages
    
    def update_channels(self, agent_name: str) -> None:
        agent = self.registry.get_agent(agent_name)
        # Get ongoing DMs and regular channels
        ongoing_dms = agent.slack_client.check_ongoing_dms()
        channels = agent.slack_client.list_channels()
        # Combine both DMs and regular channels
        all_channels = []
        existing_channel_ids = set()
        
        if ongoing_dms.get('channels'):
            for channel in ongoing_dms['channels']:
                if channel['id'] not in existing_channel_ids:
                    channel_members = agent.slack_client.get_channel_members(channel['id'])['members']
                    
                    # remove always add users from channel members
                    channel_members = [member for member in channel_members if member not in self.registry.get_always_add_users()]
                    
                    all_channels.append(Channel(slack_id=channel['id'], name=",".join(channel_members)))
                    existing_channel_ids.add(channel['id'])
                    
        if channels.get('channels'):
            for channel in channels['channels']:
                if channel['id'] not in existing_channel_ids:
                    all_channels.append(Channel(slack_id=channel['id'], name=channel['name']))
                    existing_channel_ids.add(channel['id'])
            
        # Update agent's channels with the combined list
        agent.channels = all_channels
        
    def _convert_list_of_messages_to_dict(self, messages: List[Message]) -> dict:
        return [asdict(message) for message in messages]
    
        
    def _export_agent_logs(self, agent: Agent) -> list[dict]:
        log_dir = self.registry.config['log_dir']
        world_start_datetime = self.registry.get_world(agent.world_name).start_datetime
        
        log_dir = os.path.join(log_dir, str(world_start_datetime))
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            # save the slack config and the main config in root
            with open(os.path.join(log_dir, "slack_config.json"), "w") as f:
                json.dump(self.registry.get_masked_slack_config(), f, indent=4)
            with open(os.path.join(log_dir, "config.json"), "w") as f:
                json.dump(self.registry.config, f, indent=4)
        
    
        agent_obj = self.registry.get_agent(agent.agent_name)
        logs_to_save = {agent.agent_name: {channel_id: self._convert_list_of_messages_to_dict(channel_messages) for channel_id, channel_messages in agent_obj.read_messages.items()}}
        
        channel_metadata = {}
        for channel_id, channel_messages in logs_to_save[agent.agent_name].items():
            channel_metadata[channel_id] = asdict(self.registry.get_channel_from_id(channel_id))
        
        logs_to_save['channel_metadata'] = channel_metadata
        
        with open(f"{log_dir}/{agent.agent_name}.json", "w") as f:
            json.dump(logs_to_save, f, indent=4)

    def _update_agent_read_messages(self, agent_name: str, channel_id: str, messages: List[Message]) -> None:
        
        agent = self.registry.get_agent(agent_name)
        # append any message in messages that's not already in the agent's read_messages
        agent.read_messages[channel_id].extend([message for message in messages if message not in agent.read_messages[channel_id]])
        
        self._export_agent_logs(agent)
            
            
    
    def start(self):
        """Start the server in a background thread"""
        if self.server_thread is not None:
            return  # Server already running
            
        def run_server():
            uvicorn.run(self.app, host=self.host, port=self.port)
            
        self.server_thread = threading.Thread(target=run_server)
        self.server_thread.daemon = True
        self.server_thread.start()
        time.sleep(1)  # Give the server a moment to start

    def stop(self):
        """Stop the server"""
        if self.server_thread is not None:
            self.server_thread.join(timeout=1)
            self.server_thread = None


if __name__ == "__main__":
    server = Server()
    server.start()
