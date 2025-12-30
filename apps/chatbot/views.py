from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt

from openai import OpenAI, OpenAIError

from .models import Chat, ChatSession
import json


from rest_framework import viewsets, permissions

from .serializers import ChatSessionSerializer, ChatSerializer


# Initialize OpenAI client
client = OpenAI(api_key=settings.OPENAI_API_KEY)


# ---------------------------
# Helpers
# ---------------------------

def build_conversation_history(chat_session):
    """
    Builds OpenAI-compatible message history
    from DB for a specific chat session
    """
    history = [
        {"role": "system", "content": "You are a helpful AI chat assistant."}
    ]

    chats = chat_session.chat_set.order_by("created_at")
    for chat in chats:
        history.append({"role": "user", "content": chat.message})
        history.append({"role": "assistant", "content": chat.response})

    return history


def get_ai_response(chat_session, user_message):
    """
    Sends message to OpenAI with session-based memory
    """
    messages = build_conversation_history(chat_session)
    messages.append({"role": "user", "content": user_message})

    try:
        completion = client.chat.completions.create(
            model="gpt-5.2",
            messages=messages,
            max_completion_tokens=200,
            temperature=1
        )
        return completion.choices[0].message.content

    except OpenAIError as e:
        return f"I encountered an error: {str(e)}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


# ---------------------------
# Views
# ---------------------------

@login_required
def chat(request, session_id=None):
    """
    Main chat page + message handler
    """
    chat_sessions = ChatSession.objects.filter(
        user=request.user
    ).order_by("-created_at")

    if session_id:
        chat_session = get_object_or_404(
            ChatSession, id=session_id, user=request.user
        )
    elif chat_sessions.exists():
        chat_session = chat_sessions.first()
    else:
        chat_session = ChatSession.objects.create(
            user=request.user,
            title="New Chat"
        )

    chats = chat_session.chat_set.order_by("created_at")

    if request.method == "POST":
        user_message = request.POST.get("message", "").strip()

        if not user_message or len(user_message) > 1000:
            return JsonResponse(
                {"error": "Invalid message"}, status=400
            )

        ai_reply = get_ai_response(chat_session, user_message)

        Chat.objects.create(
            chat_session=chat_session,
            message=user_message,
            response=ai_reply
        )

        return JsonResponse({
            "response": ai_reply,
            "session_id": str(chat_session.id)
        })

    return render(request, "chatbot/chat.html", {
        "chat_sessions": chat_sessions,
        "chats": chats,
        "selected_session": chat_session,
    })


@login_required
def load_chat_session(request, session_id):
    """
    Loads messages of a specific session
    """
    chat_session = get_object_or_404(
        ChatSession, id=session_id, user=request.user
    )

    chats = [
        {
            "message": chat.message,
            "response": chat.response
        }
        for chat in chat_session.chat_set.order_by("created_at")
    ]

    return JsonResponse({"chats": chats})


@csrf_exempt
@login_required
def create_chat_session(request):
    """
    Create new chat session
    """
    if request.method == "POST":
        session = ChatSession.objects.create(
            user=request.user,
            title="New Chat"
        )
        return JsonResponse({
            "success": True,
            "session_id": str(session.id),
            "session_title": session.title
        })

    return JsonResponse({"success": False}, status=400)


@csrf_exempt
@login_required
def rename_chat_session(request, session_id):
    """
    Rename chat session
    """
    if request.method != "POST":
        return JsonResponse({"success": False}, status=400)

    try:
        data = json.loads(request.body)
        new_title = data.get("title", "").strip()

        if not new_title or len(new_title) > 100:
            return JsonResponse(
                {"success": False, "error": "Invalid title"},
                status=400
            )

        session = get_object_or_404(
            ChatSession, id=session_id, user=request.user
        )
        session.title = new_title
        session.save()

        return JsonResponse({"success": True})

    except json.JSONDecodeError:
        return JsonResponse(
            {"success": False, "error": "Invalid JSON"},
            status=400
        )


@csrf_exempt
@login_required
def delete_chat_session(request, session_id):
    """
    Delete chat session
    """
    if request.method != "DELETE":
        return JsonResponse({"success": False}, status=400)

    session = get_object_or_404(
        ChatSession, id=session_id, user=request.user
    )
    session.delete()

    return JsonResponse({"success": True})




class ChatSessionViewSet(viewsets.ModelViewSet):
    serializer_class = ChatSessionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ChatSession.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ChatViewSet(viewsets.ModelViewSet):
    serializer_class = ChatSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Chat.objects.filter(
            chat_session__user=self.request.user
        )

    def perform_create(self, serializer):
        chat = serializer.save()

        # AI response
        try:
            completion = client.chat.completions.create(
                model="gpt-5.2",
                messages=[
                    {"role": "system", "content": "You are a helpful AI assistant."},
                    {"role": "user", "content": chat.message},
                ]
            )
            chat.response = completion.choices[0].message.content
            chat.save()
        except Exception as e:
            chat.response = str(e)
            chat.save()
