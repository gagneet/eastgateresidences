"""
Blog Posts / News Articles Seed Data

Creates news articles about Denman Prospect and East Gate Residences
sourced from recent Canberra news outlets.
"""
import uuid
from datetime import datetime, timezone


def get_blog_posts_seed_data(admin_user_id=None):
    """
    Returns list of blog posts/news articles to seed into the database.

    These articles are sourced from recent news about Denman Prospect
    and the surrounding Canberra area (2024-2025).

    Args:
        admin_user_id: User ID to set as author (defaults to system admin)
    """

    # Use provided admin_user_id or generate a placeholder
    author_id = admin_user_id or str(uuid.uuid4())

    blog_posts = [
        {
            'id': str(uuid.uuid4()),
            'title': 'Government ramps up density in final stage of Denman Prospect with new tender',
            'content': """The ACT Government has issued Stage 3 tenders for Denman Prospect — a major expansion delivering up to 2,950 dwellings and public amenity. The tender documentation prioritises financial return; advocates are asking for stronger sustainability and community housing measures.

The final stage of Denman Prospect has been released for tender, with plans for nearly 3,000 medium- and high-density homes, including provisions for affordable and community housing. The development will also include space for a public primary school and commercial premises.

Community groups have raised concerns about the tender process, calling for a stronger focus on sustainability and community outcomes, rather than prioritising financial returns alone. This marks a significant milestone in the completion of one of Canberra's newest and most vibrant suburbs.

Source: Region Canberra Property""",
            'excerpt': 'ACT government released Stage 3 of Denman Prospect for tender, planning nearly 3,000 medium- and high-density homes with space for a public primary school and commercial premises.',
            'cover_image': None,
            'tags': ['Denman Prospect', 'Development', 'Government', 'Housing'],
            'is_published': True,
            'author_id': author_id,
            'author_name': 'East Gate Management',
            'views': 0,
            'created_at': datetime(2025, 6, 24, tzinfo=timezone.utc).isoformat(),
            'updated_at': datetime(2025, 6, 24, tzinfo=timezone.utc).isoformat(),
            'source_url': 'https://region.com.au/government-ramps-up-density-in-final-stage-of-denman-prospect-with-new-tender/879056/'
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'Denman Prospect Stage 3: A rare opportunity to shape Canberra\'s future',
            'content': """Infrastructure Magazine outlines the Stage 3 release and its strategic importance to Canberra's Molonglo growth corridor. The site is a major opportunity for residential and mixed-use planning with sustainability at the core.

Stage 3 is a 40-hectare parcel with approval for approximately 2,950 dwellings and community amenities. Its proximity to the upcoming Molonglo Town Centre and focus on zero emissions aligns with Canberra's vision for sustainable growth.

This development represents one of the most significant opportunities to shape the future of Canberra's urban landscape, with a strong emphasis on environmental sustainability and community-focused design principles.

Source: Infrastructure Magazine (Suburban Land Agency)""",
            'excerpt': 'Stage 3 is a 40-hectare parcel with approval for about 2,950 dwellings and amenities, close to Molonglo Town Centre, with a sustainability/zero-emissions focus.',
            'cover_image': None,
            'tags': ['Infrastructure', 'Denman Prospect', 'Stage 3', 'Sustainability'],
            'is_published': True,
            'author_id': author_id,
            'author_name': 'East Gate Management',
            'views': 0,
            'created_at': datetime(2025, 7, 31, tzinfo=timezone.utc).isoformat(),
            'updated_at': datetime(2025, 7, 31, tzinfo=timezone.utc).isoformat(),
            'source_url': 'https://origin.infrastructuremagazine.com.au/denman-prospect-stage-3-a-rare-opportunity-to-shape-canberras-future/'
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'Denman Prospect: 10 years of growth, innovation and community',
            'content': """A look back at 10 years since Denman Prospect's creation. The article highlights community events, local amenities, and milestones in sustainable housing and neighbourhood-building.

Marking a decade since the suburb's establishment, Denman Prospect has become known for its vibrant community events including street festivals, Christmas picnics, Easter egg hunts, and regular park pop-ups. The development has achieved significant milestones in both housing innovation and sustainability initiatives.

The community-focused approach has created a strong sense of belonging among residents, with regular gatherings and events that bring neighbours together. From food truck festivals to seasonal celebrations, Denman Prospect has built a reputation as a family-friendly, engaged community.

Source: Capital Estate Developments""",
            'excerpt': 'A developer/community retrospective celebrating 10 years of Denman Prospect development, local events, and community milestones.',
            'cover_image': None,
            'tags': ['Community', 'Anniversary', 'Events', 'History'],
            'is_published': True,
            'author_id': author_id,
            'author_name': 'East Gate Management',
            'views': 0,
            'created_at': datetime(2025, 7, 27, tzinfo=timezone.utc).isoformat(),
            'updated_at': datetime(2025, 7, 27, tzinfo=timezone.utc).isoformat(),
            'source_url': 'https://www.denmanprospect.com.au/news/denman-prospect-10-years-of-growth-innovation-and-community'
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'How Denman Prospect grew from the ground up in 10 years',
            'content': """Region Media's feature charts the suburb's growth, focusing on the community calendar and how local events helped shape the area's identity.

Since 2015, Denman Prospect has experienced rapid growth, establishing itself as one of Canberra's most successful new suburbs. Regular community events such as Christmas picnics, Easter egg hunts, and pop-up food events featuring local favourites like Grease Monkey have created a strong community culture.

The suburb is recognised for its family-friendly amenities, active community engagement, and continuing expansion. Residents have embraced the community spirit, participating in regular gatherings and contributing to the vibrant neighbourhood atmosphere.

Source: Region Media""",
            'excerpt': 'Feature on Denman Prospect\'s development, community events (picnics, pop-ups), family amenities and local engagement.',
            'cover_image': None,
            'tags': ['History', 'Community', 'Events', 'Development'],
            'is_published': True,
            'author_id': author_id,
            'author_name': 'East Gate Management',
            'views': 0,
            'created_at': datetime(2025, 8, 4, tzinfo=timezone.utc).isoformat(),
            'updated_at': datetime(2025, 8, 4, tzinfo=timezone.utc).isoformat(),
            'source_url': 'https://region.com.au/news/how-denman-prospect-grew-from-the-ground-up-in-10-years'
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'Developer announced for final stage of Denman Prospect, construction set to start 2027',
            'content': """Local ACT-based firm TP Dynamics has been selected as the developer for the final stage of Denman Prospect, with construction scheduled to commence in 2027.

The announcement marks an important milestone in the completion of this major residential development. TP Dynamics brings local expertise and a track record of successful projects in the Canberra region.

The final stage is expected to deliver significant community benefits, including additional housing options, public amenities, and commercial facilities that will serve both new and existing residents of the broader Denman Prospect area.

Source: Canberra Times / World News Inc.""",
            'excerpt': 'Local firm announced as developer for the final stage of Denman Prospect, with construction beginning in 2027.',
            'cover_image': None,
            'tags': ['Development', 'Construction', 'News'],
            'is_published': True,
            'author_id': author_id,
            'author_name': 'East Gate Management',
            'views': 0,
            'created_at': datetime(2025, 12, 7, tzinfo=timezone.utc).isoformat(),
            'updated_at': datetime(2025, 12, 7, tzinfo=timezone.utc).isoformat(),
            'source_url': 'https://article.wn.com/view/2025/12/07/Developer_announced_for_final_stage_of_Denman_Prospect_const/'
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'Tenders Called for Final $150m Stage of ACT\'s Newest Suburb',
            'content': """The Urban Developer reports on the $150 million final stage tender for Denman Prospect, describing location benefits and the inclusion of community and affordable housing.

The final stage tender emphasises the strategic location across from Canberra's future sixth town centre, with significant investment in public, community, and affordable housing components. Nearby facilities like Denman Village shops are already serving the community, with the future Molonglo River Bridge expected by 2026 to further improve connectivity.

This substantial investment represents a commitment to creating a balanced, accessible community with diverse housing options and strong infrastructure support.

Source: The Urban Developer""",
            'excerpt': 'The Urban Developer reports on the $150m final stage tender for Denman Prospect, describing location benefits and the inclusion of community/affordable housing.',
            'cover_image': None,
            'tags': ['Tenders', 'Development', 'Denman Prospect', 'Infrastructure'],
            'is_published': True,
            'author_id': author_id,
            'author_name': 'East Gate Management',
            'views': 0,
            'created_at': datetime(2025, 7, 22, tzinfo=timezone.utc).isoformat(),
            'updated_at': datetime(2025, 7, 22, tzinfo=timezone.utc).isoformat(),
            'source_url': 'https://theurbandeveloper.com/articles/tenders-called-for-final-150m-stage-of-acts-newest-suburb'
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'Canberra home approved after bureaucratic delay',
            'content': """A Denman Prospect home has been retrospectively approved after planning delays, highlighting both the suburb's ongoing development and the challenges of meeting housing demand in the region.

The approval follows a period of regulatory review, demonstrating the complexity of urban planning processes while also showing the system's ability to find practical solutions. The case has drawn attention to the need for streamlined approval processes to keep pace with Canberra's growing housing needs.

The resolution provides a positive outcome for the homeowner while contributing to the ongoing conversation about balancing regulatory oversight with housing supply challenges.

Source: The Canberra Times""",
            'excerpt': 'Local reporting of a Denman Prospect home given retrospective approval after a planning delay, illustrating regulatory activity in the area.',
            'cover_image': None,
            'tags': ['Planning', 'Local News', 'Housing'],
            'is_published': True,
            'author_id': author_id,
            'author_name': 'East Gate Management',
            'views': 0,
            'created_at': datetime(2024, 12, 21, tzinfo=timezone.utc).isoformat(),
            'updated_at': datetime(2024, 12, 21, tzinfo=timezone.utc).isoformat(),
            'source_url': 'https://www.canberratimes.com.au/story/8587558/canberra-home-approved-after-bureaucratic-delay/'
        },
        {
            'id': str(uuid.uuid4()),
            'title': 'Denman Prospect Stage 3 - Official Release',
            'content': """Official release from the Suburban Land Agency describing Stage 3's scope, expected dwellings, community facilities and planning timeframe.

Released in June 2025, Stage 3 offers approval for up to 2,950 dwellings across a 40-hectare site, including medium-density residential, commercial spaces, schools, and parks. The development plan incorporates extensive community engagement opportunities and outlines the vision for creating one of Canberra's newest, most vibrant and sustainable suburbs.

Key features include:
- Mixed-density housing options
- Public primary school site
- Commercial and retail premises
- Parks and recreational facilities
- Strong sustainability and emissions reduction targets

Source: Suburban Land Agency""",
            'excerpt': 'Official release from the Suburban Land Agency with Stage 3 details, dwelling counts and planning intent for Denman Prospect.',
            'cover_image': None,
            'tags': ['Official', 'Suburban Land Agency', 'Stage 3', 'Planning'],
            'is_published': True,
            'author_id': author_id,
            'author_name': 'East Gate Management',
            'views': 0,
            'created_at': datetime(2025, 6, 15, tzinfo=timezone.utc).isoformat(),
            'updated_at': datetime(2025, 6, 15, tzinfo=timezone.utc).isoformat(),
            'source_url': 'https://suburbanland.act.gov.au/releases/denman-prospect-three'
        }
    ]

    return blog_posts


# Summary statistics
BLOG_POSTS_COUNT = 8
BLOG_POSTS_TAGS = [
    'Denman Prospect', 'Development', 'Government', 'Housing',
    'Infrastructure', 'Stage 3', 'Sustainability', 'Community',
    'Anniversary', 'Events', 'History', 'Construction', 'News',
    'Tenders', 'Planning', 'Local News', 'Official', 'Suburban Land Agency'
]
