"""OpenAPI request examples for SolidSET FrameworkMessage contracts."""

_FRAMEWORK_MESSAGE_EXAMPLES = {
    "meetingAgentQuestion": {
        "summary": "Question addressed to an AI resource in a meeting",
        "description": (
            "A fictitious SolidSET FrameworkMessage where a human resource asks "
            "the selected AI resource to answer inside the current meeting."
        ),
        "value": {
            "Stamp": "2026-08-23T09:30:00Z",
            "Sender": {
                "room": "00000000-0000-0000-0000-000000000000",
                "session": "55555555-5555-4555-8555-555555555555",
                "login": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "resource": "11111111-1111-4111-8111-111111111111",
                "team": "00000000-0000-0000-0000-000000000000",
                "role": "00000000-0000-0000-0000-000000000000",
                "conversationId": 0,
                "workRoom": "00000000-0000-0000-0000-000000000000",
            },
            "Destiny": {
                "room": "00000000-0000-0000-0000-000000000000",
                "session": "00000000-0000-0000-0000-000000000000",
                "login": "00000000-0000-0000-0000-000000000000",
                "resource": "00000000-0000-0000-0000-000000000000",
                "workRoom": "33333333-3333-4333-8333-333333333333",
                "dests": [
                    {
                        "login": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "resource": "11111111-1111-4111-8111-111111111111",
                        "kind": 2,
                        "sequence": 1,
                    }
                ],
            },
            "ExternalDestinations": [],
            "ExcludeSenderUser": False,
            "ExcludeSenderSession": False,
            "IncludeSenderSession": False,
            "Kind": 7,
            "IDNotification": None,
            "RawMessage": "¿Cuáles son las tareas pendientes de este proyecto?",
            "RawMessageHtml": None,
            "Importance": 1,
            "Priority": 0,
            "Modifiers": 0,
            "VisibilityLevel": 1,
            "MaskMessage": 536870914,
            "MessageMonitoring": 0,
            "Args": [990100, 0, "0"],
            "Chat": {
                "idChat2": 990100,
                "idSender": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "idSenderResource": "11111111-1111-4111-8111-111111111111",
                "rawMessage": "¿Cuáles son las tareas pendientes de este proyecto?",
                "stamp": "2026-08-23T09:30:00Z",
                "idWorkRoom": "33333333-3333-4333-8333-333333333333",
                "idMeeting": "44444444-4444-4444-8444-444444444444",
                "idChannelOrigin": "33333333-3333-4333-8333-333333333333",
                "originChannelName": "Proyecto Atlas",
                "kind": 0,
                "status": 0,
                "extraData": (
                    '{"meeting_id":"44444444-4444-4444-8444-444444444444",'
                    '"meeting_code":"M42"}'
                ),
                "destiny": [
                    {
                        "idLogin": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "idResource": "11111111-1111-4111-8111-111111111111",
                        "userName": "María Ejemplo",
                        "resourceName": "Recurso Demo",
                        "type": 1,
                        "idChannel": "33333333-3333-4333-8333-333333333333",
                        "sequence": 0,
                    },
                    {
                        "idLogin": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "idResource": "11111111-1111-4111-8111-111111111111",
                        "userName": "María Ejemplo",
                        "resourceName": "Recurso Demo [IA]",
                        "talkWithAgent": True,
                        "type": 2,
                        "idChannel": "33333333-3333-4333-8333-333333333333",
                        "sequence": 1,
                    },
                ],
                "channels": [
                    {
                        "idChannel": "33333333-3333-4333-8333-333333333333",
                        "channelName": "Proyecto Atlas",
                        "channelKind": 0,
                        "kind": 0,
                    }
                ],
                "resourceTable": [
                    {
                        "idChannel": "33333333-3333-4333-8333-333333333333",
                        "idResource": "11111111-1111-4111-8111-111111111111",
                        "idLogin": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "userName": "María Ejemplo",
                        "resourceName": "Recurso Demo",
                        "type": 1,
                        "resourceKind": 1,
                        "sequence": 0,
                    }
                ],
            },
            "WorkRoomData": {
                "id": "33333333-3333-4333-8333-333333333333",
                "kind": 0,
                "name": "Proyecto Atlas",
            },
            "Info": {
                "meeting_id": "44444444-4444-4444-8444-444444444444",
                "meeting_code": "M42",
                "country_code": "PT",
                "locale": "es-ES",
                "time_zone": "Europe/Lisbon",
            },
            "ExtraData": ('{"meeting_id":"44444444-4444-4444-8444-444444444444"}'),
            "RelatedRecordsData": [],
            "AttentionCallNotificationLevel": 0,
            "AttentionCallNotify": False,
        },
    },
    "emptyContextAdvice": {
        "summary": "Suggest next messages from channel or meeting context",
        "description": (
            "Chat and RawMessage are empty. Info.advice_mode=1 instructs the agent "
            "to review recent accessible messages from Sender/Destiny.workRoom."
        ),
        "value": {
            "Stamp": "2026-08-23T17:15:06Z",
            "Sender": {
                "session": "00000000-0000-0000-0000-000000000000",
                "login": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "resource": "11111111-1111-4111-8111-111111111111",
                "workRoom": "33333333-3333-4333-8333-333333333333",
            },
            "Destiny": {"workRoom": "33333333-3333-4333-8333-333333333333"},
            "Kind": 7,
            "RawMessage": "",
            "Importance": 1,
            "Chat": None,
            "Info": {
                "session_id": "11111111-1111-4111-8111-111111111111",
                "advice_mode": "1",
                "request_id": "66666666-6666-4666-8666-666666666666",
                "locale": "es-ES",
                "time_zone": "Europe/Lisbon",
            },
        },
    },
}

_CHAT_QUESTION_SUGGESTION_EXAMPLES = {
    "quotedMeetingMessage": {
        "summary": "Suggest replies to a quoted message in a meeting",
        "description": (
            "The current message is empty and Chat.chatQuestion contains the "
            "message for which the requester's own AI agent must suggest replies."
        ),
        "value": {
            "Stamp": "2026-08-21T21:49:30.381109Z",
            "Sender": {
                "room": "00000000-0000-0000-0000-000000000000",
                "session": "1462023a-1a1a-496d-84d2-b1fbcdea8ee1",
                "login": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                "resource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
                "team": "00000000-0000-0000-0000-000000000000",
                "role": "00000000-0000-0000-0000-000000000000",
                "conversationId": 0,
                "workRoom": "00000000-0000-0000-0000-000000000000",
            },
            "Destiny": {
                "room": "00000000-0000-0000-0000-000000000000",
                "session": "00000000-0000-0000-0000-000000000000",
                "login": "00000000-0000-0000-0000-000000000000",
                "resource": "00000000-0000-0000-0000-000000000000",
                "team": "00000000-0000-0000-0000-000000000000",
                "role": "00000000-0000-0000-0000-000000000000",
                "conversationId": 0,
                "workRoom": "debf64b2-3b3e-eb11-870c-d850e63f5833",
                "dests": [
                    {
                        "login": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                        "resource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
                        "kind": 2,
                        "sequence": 1,
                    },
                    {
                        "login": "a13c4c94-13f8-4fad-9b6f-ec4a84e6fee7",
                        "resource": "ba55b081-3e30-4f38-9816-194720c6701f",
                        "kind": 2,
                    },
                    {
                        "login": "00000000-0000-0000-0000-000000000000",
                        "resource": "4b7ef7e2-7972-4f94-b576-bf3e8f2b3997",
                        "kind": 2,
                    },
                    {
                        "login": "00000000-0000-0000-0000-000000000000",
                        "resource": "2dcf6097-a582-4dc3-b4be-d53bb0897461",
                        "kind": 2,
                    },
                ],
            },
            "ExternalDestinations": [],
            "ExcludeSenderUser": False,
            "ExcludeSenderSession": False,
            "IncludeSenderSession": False,
            "Kind": 7,
            "IDNotification": None,
            "RawMessage": "",
            "RawMessageHtml": None,
            "Importance": 1,
            "Priority": 0,
            "Modifiers": 0,
            "VisibilityLevel": 1,
            "MaskMessage": 536870914,
            "MessageMonitoring": 0,
            "Args": [1824995, 0, "0"],
            "PointData": None,
            "Chat": {
                "idChat2": 1824995,
                "idSender": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                "idSenderResource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
                "rawMessage": "",
                "stamp": "2026-08-21T21:49:30.3811094Z",
                "idActivityConversation": 0,
                "importance": 1,
                "isPublic": 1,
                "attentionCallNotificationLevel": 0,
                "status": 0,
                "chatQuestionMessage": 1824994,
                "kind": 0,
                "readByCurrentResource": True,
                "messageStateForCurrentResource": 3,
                "isBookMarked": False,
                "canBookMark": True,
                "canUnBookmark": False,
                "chatQuestion": {
                    "idChat2": 1824994,
                    "idSender": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                    "idSenderResource": "2dcf6097-a582-4dc3-b4be-d53bb0897461",
                    "rawMessage": "Hoje é sexta-feira, 21 de agosto do ano de 2026. Horário de Brasília: 05:32.",
                    "stamp": "2026-08-21T10:45:54.673",
                    "idActivityConversation": 0,
                    "importance": 1,
                    "isPublic": 1,
                    "attentionCallNotificationLevel": 0,
                    "status": 0,
                    "kind": 0,
                    "readByCurrentResource": False,
                    "messageStateForCurrentResource": 3,
                    "isBookMarked": False,
                    "canBookMark": True,
                    "canUnBookmark": False,
                    "idWorkRoom": "debf64b2-3b3e-eb11-870c-d850e63f5833",
                    "idMeeting": "7d7a581d-d7c1-4e18-a11b-6d322e4755c6",
                    "messageMonitoring": 0,
                    "maskMessage": 0,
                    "questionType": 0,
                    "questionStatus": 0,
                    "questionCloseRequested": 0,
                    "externalChannels": 0,
                },
                "idWorkRoom": "debf64b2-3b3e-eb11-870c-d850e63f5833",
                "idMeeting": "7d7a581d-d7c1-4e18-a11b-6d322e4755c6",
                "idChannelOrigin": "debf64b2-3b3e-eb11-870c-d850e63f5833",
                "originChannelName": "SSET Communicator",
                "messageMonitoring": 0,
                "maskMessage": 0,
                "extraData": '{"meeting_id":"7d7a581d-d7c1-4e18-a11b-6d322e4755c6","meeting_code":"M11"}',
                "destiny": [
                    {
                        "idLogin": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                        "idResource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
                        "type": 1,
                        "idChannel": "debf64b2-3b3e-eb11-870c-d850e63f5833",
                        "isOriginMessageSender": False,
                        "sequence": 0,
                        "action": 0,
                    },
                    {
                        "idLogin": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                        "userName": "Alejandro Veitia",
                        "idResource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
                        "resourceName": "Dev17 [IA]",
                        "talkWithAgent": True,
                        "type": 2,
                        "idChannel": "debf64b2-3b3e-eb11-870c-d850e63f5833",
                        "isOriginMessageSender": False,
                        "sequence": 1,
                        "action": 0,
                    },
                ],
                "questionType": 0,
                "questionStatus": 0,
                "questionCloseRequested": 0,
                "externalChannels": 0,
                "channels": [
                    {
                        "idChannel": "debf64b2-3b3e-eb11-870c-d850e63f5833",
                        "channelName": "SSET Communicator",
                        "channelKind": 0,
                        "kind": 0,
                    }
                ],
                "resourceTable": [
                    {
                        "idChannel": "debf64b2-3b3e-eb11-870c-d850e63f5833",
                        "idResource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
                        "idLogin": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                        "userName": "Alejandro Veitia",
                        "resourceName": "Dev17",
                        "type": 1,
                        "resourceKind": 1,
                        "sequence": 0,
                    },
                    {
                        "idChannel": "debf64b2-3b3e-eb11-870c-d850e63f5833",
                        "idResource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
                        "idLogin": "1790fc78-023d-4506-a7e8-5c030e9386d1",
                        "userName": "Alejandro Veitia",
                        "resourceName": "Dev17 [IA]",
                        "type": 2,
                        "resourceKind": 1,
                        "sequence": 1,
                    },
                ],
            },
            "UserData": None,
            "ChatReadData": None,
            "ImportanceSettingData": None,
            "NotificationSettingsData": None,
            "MailData": None,
            "CompanyData": None,
            "VideoCallData": None,
            "MeetingData": None,
            "TaskData": None,
            "ActivityData": None,
            "Task": None,
            "ScheduleActivity": None,
            "ChatData": {"idChat": 1824994},
            "ChatTransferingData": None,
            "ScheduledData": None,
            "WorkRoomData": {
                "id": "debf64b2-3b3e-eb11-870c-d850e63f5833",
                "kind": 0,
                "name": "SSET Communicator",
            },
            "RecordData": None,
            "ObjectContent": None,
            "IDChatExtVars": None,
            "Info": {
                "meeting_id": "7d7a581d-d7c1-4e18-a11b-6d322e4755c6",
                "meeting_code": "M11",
            },
            "ExtraData": '{"meeting_id":"7d7a581d-d7c1-4e18-a11b-6d322e4755c6"}',
            "LinkData": None,
            "TimeData": None,
            "FeatureFlagData": None,
            "RelatedRecordsData": [],
            "ReminderData": None,
            "AttentionCallNotificationLevel": 0,
            "AttentionCallNotify": False,
            "NotifyDate": None,
            "DebugData": None,
            "TreatLaterNotifData": None,
        },
    }
}

_CHAT_QUESTION_SUGGESTION_EXAMPLES = {
    "quotedMeetingMessage": {
        "summary": "Suggest replies to a quoted message in a meeting",
        "description": (
            "The current message is empty. Chat.chatQuestion contains the "
            "previous message for which the requester's own agent suggests replies."
        ),
        "value": {
            "Stamp": "2026-08-23T10:15:00Z",
            "Sender": {
                "session": "55555555-5555-4555-8555-555555555555",
                "login": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "resource": "11111111-1111-4111-8111-111111111111",
                "workRoom": "33333333-3333-4333-8333-333333333333",
            },
            "Destiny": {
                "workRoom": "33333333-3333-4333-8333-333333333333",
                "dests": [
                    {
                        "login": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "resource": "11111111-1111-4111-8111-111111111111",
                        "kind": 2,
                    }
                ],
            },
            "Kind": 7,
            "RawMessage": "",
            "Importance": 1,
            "Args": [990002, 0, "0"],
            "Chat": {
                "idChat2": 990002,
                "idSender": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "idSenderResource": "11111111-1111-4111-8111-111111111111",
                "rawMessage": "",
                "stamp": "2026-08-23T10:15:00Z",
                "chatQuestionMessage": 990001,
                "chatQuestion": {
                    "idChat2": 990001,
                    "idSender": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                    "idSenderResource": "22222222-2222-4222-8222-222222222222",
                    "rawMessage": (
                        "¿Podemos confirmar los responsables y el plazo de entrega?"
                    ),
                    "stamp": "2026-08-23T10:10:00Z",
                    "idWorkRoom": "33333333-3333-4333-8333-333333333333",
                    "idMeeting": "44444444-4444-4444-8444-444444444444",
                },
                "idWorkRoom": "33333333-3333-4333-8333-333333333333",
                "idMeeting": "44444444-4444-4444-8444-444444444444",
                "originChannelName": "Proyecto Atlas",
                "destiny": [
                    {
                        "idLogin": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "idResource": "11111111-1111-4111-8111-111111111111",
                        "userName": "María Ejemplo",
                        "resourceName": "Recurso Demo",
                        "type": 1,
                        "sequence": 0,
                    },
                    {
                        "idLogin": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "idResource": "11111111-1111-4111-8111-111111111111",
                        "resourceName": "Recurso Demo [IA]",
                        "talkWithAgent": True,
                        "type": 2,
                        "sequence": 1,
                    },
                ],
            },
            "WorkRoomData": {
                "id": "33333333-3333-4333-8333-333333333333",
                "kind": 0,
                "name": "Proyecto Atlas",
            },
            "Info": {
                "meeting_id": "44444444-4444-4444-8444-444444444444",
                "meeting_code": "M42",
                "locale": "es-ES",
                "time_zone": "Europe/Lisbon",
            },
        },
    }
}

_CHAT_QUESTION_SUGGESTION_EXAMPLES["emptyContextAdvice"] = _FRAMEWORK_MESSAGE_EXAMPLES[
    "emptyContextAdvice"
]
