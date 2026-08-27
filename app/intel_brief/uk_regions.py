"""Every UK region this can follow, and the local publishers for each.

The point of this file is that nothing about where the operator lives is
committed to the repository. `LOCAL_PLACES` in .env names the places that
matter to one reader; everything downstream -- which BBC region to poll, which
regional paper, what counts as a "local" story -- is derived from that at
install time. A clone contains all 41 regions and prefers none of them.

Place lists follow one rule, the same one geo.py uses: a name that is also a
common English word, or a bigger place somewhere else, costs more in false
positives than it earns in coverage. So no Reading, Bath, Wells, Street, Leek
or March; no Boston, Washington, Perth or Christchurch; no Rugby, and no
Westminster, which in a headline means Parliament rather than the borough.

Every feed here was fetched and confirmed to return items before being added.
BBC regional feeds are the backbone because they exist for all 41 regions and
are stable; the papers are added where a region has one with a working feed.
"""

BBC_ENGLAND = "https://feeds.bbci.co.uk/news/england/{}/rss.xml"
BBC_NATION = "https://feeds.bbci.co.uk/news/{}/rss.xml"
REACH = "https://www.{}/news/?service=rss"


def _bbc(slug: str, label: str) -> dict:
    return {"name": f"BBC News - {label}", "url": BBC_ENGLAND.format(slug),
            "outlet": f"BBC {label}", "outlet_type": "mainstream"}


def _nation(slug: str, label: str) -> dict:
    return {"name": f"BBC News - {label}", "url": BBC_NATION.format(slug),
            "outlet": f"BBC {label}", "outlet_type": "mainstream"}


def _paper(name: str, domain: str, url: str = "") -> dict:
    return {"name": name, "url": url or REACH.format(domain),
            "outlet": name, "outlet_type": "mainstream"}


REGIONS: list[dict] = [
    {"id": "beds_bucks_herts", "label": "Bedfordshire, Buckinghamshire & Hertfordshire",
     "feeds": [_bbc("beds_bucks_and_herts", "Beds, Bucks & Herts")],
     "places": ["Bedfordshire", "Buckinghamshire", "Hertfordshire", "Luton", "Bedford",
                "Milton Keynes", "Aylesbury", "Watford", "Stevenage", "Hemel Hempstead",
                "St Albans", "High Wycombe", "Dunstable"]},
    {"id": "berkshire", "label": "Berkshire",
     "feeds": [_bbc("berkshire", "Berkshire")],
     "places": ["Berkshire", "Slough", "Bracknell", "Maidenhead", "Newbury", "Windsor",
                "Wokingham", "Thatcham"]},
    {"id": "birmingham", "label": "Birmingham & Black Country",
     "feeds": [_bbc("birmingham_and_black_country", "Birmingham"),
               _paper("Birmingham Mail", "birminghammail.co.uk")],
     "places": ["Birmingham", "Black Country", "Wolverhampton", "Dudley", "Walsall",
                "West Bromwich", "Solihull", "Sandwell", "Brummie"]},
    {"id": "bristol", "label": "Bristol",
     "feeds": [_bbc("bristol", "Bristol"), _paper("Bristol Post", "bristolpost.co.uk")],
     "places": ["Bristol", "Avonmouth", "Bedminster", "Filton", "Clevedon", "Portishead"]},
    {"id": "cambridgeshire", "label": "Cambridgeshire",
     "feeds": [_bbc("cambridgeshire", "Cambridgeshire"),
               _paper("Cambridgeshire Live", "cambridge-news.co.uk")],
     "places": ["Cambridgeshire", "Cambridge", "Peterborough", "Ely", "Huntingdon",
                "Wisbech", "St Neots"]},
    {"id": "cornwall", "label": "Cornwall",
     "feeds": [_bbc("cornwall", "Cornwall")],
     "places": ["Cornwall", "Cornish", "Truro", "Falmouth", "Penzance", "Newquay",
                "St Austell", "Bodmin", "Camborne", "Redruth", "Launceston", "Liskeard",
                "Saltash", "Bude", "Wadebridge", "Padstow", "St Ives", "Helston", "Hayle",
                "Looe", "Fowey", "Isles of Scilly"]},
    {"id": "coventry", "label": "Coventry & Warwickshire",
     "feeds": [_bbc("coventry_and_warwickshire", "Coventry & Warwickshire")],
     "places": ["Coventry", "Warwickshire", "Warwick", "Nuneaton", "Leamington Spa",
                "Stratford-upon-Avon", "Bedworth", "Kenilworth"]},
    {"id": "cumbria", "label": "Cumbria",
     "feeds": [_bbc("cumbria", "Cumbria")],
     "places": ["Cumbria", "Carlisle", "Barrow-in-Furness", "Kendal", "Whitehaven",
                "Workington", "Penrith", "Windermere", "Lake District", "Ambleside"]},
    {"id": "derbyshire", "label": "Derbyshire",
     "feeds": [_bbc("derbyshire", "Derbyshire"),
               _paper("Derby Telegraph", "derbytelegraph.co.uk")],
     "places": ["Derbyshire", "Derby", "Chesterfield", "Buxton", "Glossop", "Ilkeston",
                "Swadlincote", "Peak District", "Matlock"]},
    {"id": "devon", "label": "Devon",
     "feeds": [_bbc("devon", "Devon")],
     "places": ["Devon", "Devonian", "Plymouth", "Exeter", "Torquay", "Paignton", "Torbay",
                "Barnstaple", "Bideford", "Ilfracombe", "Tiverton", "Exmouth", "Sidmouth",
                "Dawlish", "Teignmouth", "Newton Abbot", "Totnes", "Dartmouth",
                "Kingsbridge", "Salcombe", "Brixham", "Ivybridge", "Tavistock",
                "Okehampton", "Honiton", "Dartmoor", "Exmoor"]},
    {"id": "dorset", "label": "Dorset",
     "feeds": [_bbc("dorset", "Dorset")],
     "places": ["Dorset", "Bournemouth", "Poole", "Weymouth", "Dorchester", "Bridport",
                "Sherborne", "Swanage", "Blandford Forum"]},
    {"id": "essex", "label": "Essex",
     "feeds": [_bbc("essex", "Essex"), _paper("Essex Live", "essexlive.news")],
     "places": ["Essex", "Chelmsford", "Colchester", "Southend", "Basildon", "Harlow",
                "Brentwood", "Clacton", "Braintree", "Grays", "Romford"]},
    {"id": "gloucestershire", "label": "Gloucestershire",
     "feeds": [_bbc("gloucestershire", "Gloucestershire"),
               _paper("Gloucestershire Live", "gloucestershirelive.co.uk")],
     "places": ["Gloucestershire", "Gloucester", "Cheltenham", "Stroud", "Cirencester",
                "Tewkesbury", "Forest of Dean", "Cotswolds"]},
    {"id": "hampshire", "label": "Hampshire & Isle of Wight",
     "feeds": [_bbc("hampshire", "Hampshire")],
     "places": ["Hampshire", "Southampton", "Portsmouth", "Winchester", "Basingstoke",
                "Andover", "Aldershot", "Farnborough", "Eastleigh", "Fareham", "Gosport",
                "Isle of Wight", "Ryde", "Cowes", "Havant"]},
    {"id": "hereford_worcester", "label": "Herefordshire & Worcestershire",
     "feeds": [_bbc("hereford_and_worcester", "Hereford & Worcester")],
     "places": ["Herefordshire", "Worcestershire", "Hereford", "Worcester", "Kidderminster",
                "Redditch", "Malvern", "Bromsgrove", "Evesham", "Ledbury"]},
    {"id": "humberside", "label": "Humberside",
     "feeds": [_bbc("humberside", "Humberside"),
               _paper("Hull Daily Mail", "hulldailymail.co.uk")],
     "places": ["Humberside", "Hull", "Grimsby", "Scunthorpe", "Beverley", "Bridlington",
                "Goole", "Cleethorpes", "Humber"]},
    {"id": "kent", "label": "Kent",
     "feeds": [_bbc("kent", "Kent"), _paper("Kent Live", "kentlive.news")],
     "places": ["Kent", "Canterbury", "Maidstone", "Dover", "Folkestone", "Margate",
                "Ramsgate", "Ashford", "Gravesend", "Tunbridge Wells", "Chatham",
                "Sittingbourne", "Whitstable", "Medway"]},
    {"id": "lancashire", "label": "Lancashire",
     "feeds": [_bbc("lancashire", "Lancashire")],
     "places": ["Lancashire", "Preston", "Blackpool", "Blackburn", "Burnley", "Lancaster",
                "Chorley", "Accrington", "Morecambe", "Ormskirk", "Fylde"]},
    {"id": "leeds", "label": "Leeds & West Yorkshire",
     "feeds": [_bbc("leeds_and_west_yorkshire", "Leeds"),
               _paper("Yorkshire Post", "yorkshirepost.co.uk", "https://www.yorkshirepost.co.uk/rss")],
     "places": ["Leeds", "West Yorkshire", "Bradford", "Wakefield", "Huddersfield",
                "Dewsbury", "Keighley", "Batley", "Pontefract", "Ilkley"]},
    {"id": "leicester", "label": "Leicester & Rutland",
     "feeds": [_bbc("leicester", "Leicester"),
               _paper("Leicester Mercury", "leicestermercury.co.uk")],
     "places": ["Leicester", "Leicestershire", "Loughborough", "Hinckley", "Melton Mowbray",
                "Coalville", "Market Harborough", "Rutland", "Oakham"]},
    {"id": "lincolnshire", "label": "Lincolnshire",
     "feeds": [_bbc("lincolnshire", "Lincolnshire")],
     "places": ["Lincolnshire", "Lincoln", "Grantham", "Skegness", "Spalding", "Louth",
                "Sleaford", "Gainsborough"]},
    {"id": "london", "label": "London",
     "feeds": [_bbc("london", "London"),
               _paper("The Standard", "standard.co.uk", "https://www.standard.co.uk/rss")],
     "places": ["London", "Greater London", "Londoner", "Londoners", "Hackney", "Islington",
                "Southwark", "Lambeth", "Tower Hamlets", "Wandsworth", "Hammersmith",
                "Shoreditch", "Whitechapel", "Peckham", "Brixton", "Clapham", "Croydon",
                "Ealing", "Haringey", "Newham", "Barnet", "Enfield", "Hounslow",
                "Hillingdon", "Uxbridge", "Wembley", "Camden Town", "Notting Hill",
                "Canary Wharf", "Heathrow"]},
    {"id": "manchester", "label": "Greater Manchester",
     "feeds": [_bbc("manchester", "Manchester"),
               _paper("Manchester Evening News", "manchestereveningnews.co.uk")],
     "places": ["Manchester", "Greater Manchester", "Mancunian", "Salford", "Stockport",
                "Oldham", "Rochdale", "Bolton", "Wigan", "Tameside", "Trafford",
                "Altrincham", "Ashton-under-Lyne", "Sale"]},
    {"id": "merseyside", "label": "Merseyside",
     "feeds": [_bbc("merseyside", "Merseyside"),
               _paper("Liverpool Echo", "liverpoolecho.co.uk")],
     "places": ["Merseyside", "Liverpool", "Wirral", "Birkenhead", "St Helens", "Southport",
                "Bootle", "Kirkby", "Wallasey", "Scouse"]},
    {"id": "norfolk", "label": "Norfolk",
     "feeds": [_bbc("norfolk", "Norfolk")],
     "places": ["Norfolk", "Norwich", "Great Yarmouth", "King's Lynn", "Thetford",
                "Dereham", "Cromer", "Diss", "Broads"]},
    {"id": "northamptonshire", "label": "Northamptonshire",
     "feeds": [_bbc("northamptonshire", "Northamptonshire")],
     "places": ["Northamptonshire", "Northampton", "Kettering", "Corby", "Wellingborough",
                "Daventry", "Rushden"]},
    {"id": "nottingham", "label": "Nottinghamshire",
     "feeds": [_bbc("nottingham", "Nottingham"),
               _paper("Nottingham Post", "nottinghampost.com")],
     "places": ["Nottingham", "Nottinghamshire", "Mansfield", "Newark", "Worksop",
                "Retford", "Beeston", "Hucknall"]},
    {"id": "oxford", "label": "Oxfordshire",
     "feeds": [_bbc("oxford", "Oxford")],
     "places": ["Oxford", "Oxfordshire", "Banbury", "Bicester", "Abingdon", "Witney",
                "Didcot", "Henley-on-Thames"]},
    {"id": "shropshire", "label": "Shropshire",
     "feeds": [_bbc("shropshire", "Shropshire")],
     "places": ["Shropshire", "Shrewsbury", "Telford", "Oswestry", "Bridgnorth", "Ludlow",
                "Market Drayton"]},
    {"id": "somerset", "label": "Somerset",
     "feeds": [_bbc("somerset", "Somerset")],
     "places": ["Somerset", "Taunton", "Yeovil", "Bridgwater", "Glastonbury", "Frome",
                "Minehead", "Burnham-on-Sea", "Mendip"]},
    {"id": "south_yorkshire", "label": "South Yorkshire",
     "feeds": [_bbc("south_yorkshire", "South Yorkshire")],
     "places": ["South Yorkshire", "Sheffield", "Doncaster", "Rotherham", "Barnsley"]},
    {"id": "staffordshire", "label": "Stoke & Staffordshire",
     "feeds": [_bbc("stoke_and_staffordshire", "Stoke & Staffordshire"),
               _paper("Stoke Sentinel", "stokesentinel.co.uk")],
     "places": ["Staffordshire", "Stoke-on-Trent", "Stafford", "Burton upon Trent",
                "Newcastle-under-Lyme", "Lichfield", "Tamworth", "Cannock", "Potteries"]},
    {"id": "suffolk", "label": "Suffolk",
     "feeds": [_bbc("suffolk", "Suffolk")],
     "places": ["Suffolk", "Ipswich", "Bury St Edmunds", "Lowestoft", "Felixstowe",
                "Newmarket", "Sudbury", "Haverhill"]},
    {"id": "sussex", "label": "Sussex",
     "feeds": [_bbc("sussex", "Sussex")],
     "places": ["Sussex", "Brighton", "Hove", "Eastbourne", "Hastings", "Worthing",
                "Crawley", "Chichester", "Bognor Regis", "Horsham", "Lewes"]},
    {"id": "tees", "label": "Teesside",
     "feeds": [_bbc("tees", "Tees")],
     "places": ["Teesside", "Middlesbrough", "Stockton-on-Tees", "Redcar", "Hartlepool",
                "Darlington", "Billingham", "Cleveland"]},
    {"id": "tyne_wear", "label": "Tyne & Wear",
     "feeds": [_bbc("tyne_and_wear", "Tyne & Wear"),
               _paper("ChronicleLive", "chroniclelive.co.uk")],
     "places": ["Tyne and Wear", "Newcastle upon Tyne", "Gateshead", "Sunderland",
                "South Shields", "Whitley Bay", "Jarrow", "Tynemouth", "Geordie",
                "Northumberland"]},
    {"id": "wiltshire", "label": "Wiltshire",
     "feeds": [_bbc("wiltshire", "Wiltshire")],
     "places": ["Wiltshire", "Swindon", "Salisbury", "Chippenham", "Trowbridge", "Devizes",
                "Marlborough", "Stonehenge"]},
    {"id": "york", "label": "York & North Yorkshire",
     "feeds": [_bbc("york_and_north_yorkshire", "York")],
     "places": ["York", "North Yorkshire", "Harrogate", "Scarborough", "Northallerton",
                "Selby", "Whitby", "Ripon", "Skipton", "Malton"]},
    {"id": "scotland", "label": "Scotland",
     "feeds": [_nation("scotland", "Scotland"),
               _paper("Glasgow Live", "glasgowlive.co.uk"),
               _paper("Edinburgh Live", "edinburghlive.co.uk")],
     "places": ["Scotland", "Scottish", "Scots", "Glasgow", "Edinburgh", "Aberdeen",
                "Dundee", "Inverness", "Stirling", "Falkirk", "Paisley", "Fife",
                "Highlands", "Hebrides", "Orkney", "Shetland", "Holyrood", "Ayrshire",
                "Lanarkshire"]},
    {"id": "wales", "label": "Wales",
     "feeds": [_nation("wales", "Wales"), _paper("WalesOnline", "walesonline.co.uk")],
     "places": ["Wales", "Welsh", "Cardiff", "Swansea", "Wrexham", "Aberystwyth",
                "Merthyr Tydfil", "Rhondda", "Caerphilly", "Bridgend", "Llandudno",
                "Senedd", "Gwynedd", "Powys", "Pembrokeshire", "Snowdonia", "Eryri",
                "Neath", "Port Talbot"]},
    {"id": "northern_ireland", "label": "Northern Ireland",
     "feeds": [_nation("northern_ireland", "Northern Ireland"),
               _paper("Belfast Live", "belfastlive.co.uk")],
     "places": ["Northern Ireland", "Belfast", "Derry", "Londonderry", "Lisburn", "Newry",
                "Armagh", "Ballymena", "Coleraine", "Enniskillen", "Omagh", "Stormont",
                "Ulster", "Antrim", "Fermanagh"]},
]

REGIONS_BY_ID: dict[str, dict] = {r["id"]: r for r in REGIONS}


def all_places() -> list[str]:
    """Every place name across every region, for documentation and validation."""
    seen: list[str] = []
    for r in REGIONS:
        for p in r["places"]:
            if p not in seen:
                seen.append(p)
    return seen


def regions_for_places(places) -> list[dict]:
    """Regions whose place list overlaps the given names, in REGIONS order.

    Case-insensitive, and matched on the whole name rather than a substring so
    that "York" does not select "New York" style entries in either direction.
    """
    wanted = {p.strip().lower() for p in places if p and p.strip()}
    return [r for r in REGIONS
            if wanted & {p.lower() for p in r["places"]}]


def feeds_for_places(places) -> list[dict]:
    """Local feed definitions implied by LOCAL_PLACES, de-duplicated by URL."""
    out: list[dict] = []
    seen: set[str] = set()
    for r in regions_for_places(places):
        for f in r["feeds"]:
            if f["url"] in seen:
                continue
            seen.add(f["url"])
            out.append({**f, "category_hint": "local", "country": "GB", "enabled": True})
    return out


def unmatched(places) -> list[str]:
    """Names in LOCAL_PLACES that belong to no known region.

    Not an error -- a village too small to be listed still works for
    classification -- but install.sh reports them, because a whole LOCAL_PLACES
    that matches nothing means no local feed gets enabled at all.
    """
    known = {p.lower() for p in all_places()}
    return [p for p in places if p.strip() and p.strip().lower() not in known]
