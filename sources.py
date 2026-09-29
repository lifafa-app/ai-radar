"""Where the radar looks every morning.

kind:
  rss          RSS or Atom feed
  page         HTML listing page; new article links are detected against state/seen.json
  hn           Hacker News stories from the last day (AI-related only)
  hf_trending  Trending models on Hugging Face
tier:   1 = primary lab source, 2 = strong signal, 3 = extra coverage
filter: True = keep only items that mention AI keywords (for broad feeds)
"""

SOURCES = [
    # Labs and model makers
    {"name": "OpenAI", "kind": "rss", "url": "https://openai.com/news/rss.xml", "tier": 1},
    {"name": "Anthropic", "kind": "page", "url": "https://www.anthropic.com/news",
     "base": "https://www.anthropic.com", "pattern": r"^/news/[a-z0-9-]+/?$", "tier": 1},
    {"name": "Google AI", "kind": "rss", "url": "https://blog.google/technology/ai/rss/", "tier": 1, "filter": True},
    {"name": "Google DeepMind", "kind": "rss", "url": "https://blog.google/technology/google-deepmind/rss/", "tier": 1},
    {"name": "Google Developers", "kind": "rss", "url": "https://developers.googleblog.com/feeds/posts/default",
     "tier": 2, "filter": True},
    {"name": "Mistral", "kind": "page", "url": "https://mistral.ai/news",
     "base": "https://mistral.ai", "pattern": r"^/news/[a-z0-9-]+/?$", "tier": 1},
    {"name": "xAI", "kind": "page", "url": "https://x.ai/news",
     "base": "https://x.ai", "pattern": r"^/news/[a-z0-9-]+/?$", "tier": 1},
    {"name": "DeepSeek", "kind": "page", "url": "https://api-docs.deepseek.com/news/news",
     "base": "https://api-docs.deepseek.com", "pattern": r"^/news/news\d+/?$", "tier": 1},

    # Builders and open models
    {"name": "Hugging Face blog", "kind": "rss", "url": "https://huggingface.co/blog/feed.xml", "tier": 2},
    {"name": "Hugging Face trending", "kind": "hf_trending",
     "url": "https://huggingface.co/api/models?sort=trendingScore&limit=15", "tier": 2},
    {"name": "Simon Willison", "kind": "rss", "url": "https://simonwillison.net/atom/everything/", "tier": 2},
    {"name": "Hacker News", "kind": "hn", "tier": 2},

    # Communities (often blocked from cloud servers; failures are listed in the email)
    {"name": "r/LocalLLaMA", "kind": "rss", "url": "https://www.reddit.com/r/LocalLLaMA/top/.rss?t=day", "tier": 3},
    {"name": "r/ClaudeAI", "kind": "rss", "url": "https://www.reddit.com/r/ClaudeAI/top/.rss?t=day", "tier": 3},
    {"name": "r/OpenAI", "kind": "rss", "url": "https://www.reddit.com/r/OpenAI/top/.rss?t=day", "tier": 3},
    {"name": "r/GeminiAI", "kind": "rss", "url": "https://www.reddit.com/r/GeminiAI/top/.rss?t=day", "tier": 3},

    # News
    {"name": "TechCrunch AI", "kind": "rss", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "tier": 2},
    {"name": "The Verge AI", "kind": "rss", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "tier": 2},
    {"name": "The Decoder", "kind": "rss", "url": "https://the-decoder.com/feed/", "tier": 2},
    {"name": "MarkTechPost", "kind": "rss", "url": "https://www.marktechpost.com/feed/", "tier": 3},
    {"name": "AWS Machine Learning", "kind": "rss", "url": "https://aws.amazon.com/blogs/machine-learning/feed/",
     "tier": 3, "filter": True},
    {"name": "NVIDIA", "kind": "rss", "url": "https://blogs.nvidia.com/feed/", "tier": 3, "filter": True},
    {"name": "Product Hunt", "kind": "rss", "url": "https://www.producthunt.com/feed", "tier": 3, "filter": True},
]

AI_KEYWORDS = (
    r"\b(AI|LLMs?|GPT[-\w.]*|ChatGPT|Claude|Anthropic|Gemini|OpenAI|Llama|Mistral|DeepSeek|Qwen|Grok|xAI|"
    r"Perplexity|Copilot|chatbots?|agents?|agentic|benchmarks?|reasoning|inference|open[- ]weights?|"
    r"Sora|Veo|Midjourney|Cursor|Codex|Kimi|GLM|Hugging Face|multimodal|text-to-speech|TTS)\b"
)

# Words that suggest a launch or a big update; used by the no-LLM fallback ranking.
LAUNCH_WORDS = r"\b(introducing|launch(es|ed)?|release[sd]?|now available|announc(es|ed|ing)|unveil(s|ed)?|new model|rolls? out|open[- ]sources?)\b"
