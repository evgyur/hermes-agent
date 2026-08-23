# Content Syndication

One source → X/LinkedIn/Reddit versions with different angles.

## Source: Blog Post

### X/X/Twitter Thread
- 8-12 tweets
- Hook: contrarian or curiosity gap
- Each tweet: one idea, max 280 chars
- End: CTA to full post

**Template:**
```
Tweet 1 (Hook): {controversial_take_or_question}

Tweet 2-3: {setup_context}

Tweet 4-7: {key_points_as_thread}

Tweet 8-10: {examples_or_proof}

Tweet 11: {key_insight}

Tweet 12: {CTA_to_full_post}
```

### LinkedIn Post
- Professional tone
- Story format preferred
- Tag relevant people
- Use 3-5 hashtags

**Template:**
```
{personal_story_or_observation}

{lesson_learned}

{how_it_applies_to_reader}

{CTA_or_question}

#hashtag1 #hashtag2 #hashtag3
```

### Reddit (r/SideProject or r/Entrepreneur)
- Casual, authentic tone
- Lead with problem, not solution
- Ask for feedback, not promotion
- Respond to every comment

**Template:**
```
I spent {time} building {solution} to solve {pain_point}.

The problem: {detailed_problem_description}

What I built: {solution_overview}

Key insight: {surprising_discovery}

What would you do differently?
```

## Source: Video

### X/X/Twitter
- Clip the best 60 seconds
- Add captions
- Hook in first 3 seconds
- End with "Full video in bio"

### LinkedIn
- Write summary post
- Embed video
- Add timestamps in comments
- Ask specific question

### Blog Post
- Transcribe video
- Add screenshots
- Expand on key points
- Include code/examples

## Automation

```python
class ContentSyndicator:
    def syndicate(self, source: Content, platforms: List[Platform]):
        for platform in platforms:
            angle = self.select_angle(source, platform.audience)
            adapted = self.adapt(source, angle, platform.format)
            platform.post(adapted)
```

## Usage

```bash
# Syndicate blog post
/syndicate --source blog_post.md --platforms x,linkedin,reddit

# Custom angles
/syndicate --source blog_post.md --angles "controversial,practical,story"
```
