# UK regions: what to put in `LOCAL_PLACES`


`LOCAL_PLACES` in `.env` does two jobs. It decides which stories get filed
under **Local** rather than Country or Global, and at install time it decides
which regional publishers get polled at all — the installer matches your place
names against the table below and writes the matching feeds into `feeds.yaml`.

Set it to the places you actually care about. You do not need to use a whole
row; one recognisable name is enough to select a region, and extra names that
match no region still work for classification.

```bash
LOCAL_PLACES="Manchester,Salford,Stockport"
```

Two rules worth knowing:

- **Whole words, not substrings.** `London` does not match `Londonderry`.
- **Ambiguous names are deliberately absent.** Places that are also common
  English words (Reading, Bath, Wells, Street, March, Leek, Rugby) or bigger
  places elsewhere (Boston, Washington, Perth, Christchurch) are left out,
  because the false positives cost more than the coverage. `Westminster` is
  excluded because in a headline it means Parliament. You can still put any of
  them in `LOCAL_PLACES` yourself — this table only governs which *feeds* get
  selected.

If nothing matches, the installer says so and writes no local feeds. Local
classification still works off the names you gave.


## The 41 regions

61 feeds across 41 regions, every one checked live before inclusion.

| Region | Feeds selected | Place names that select it |
|---|---|---|
| **Bedfordshire, Buckinghamshire & Hertfordshire** | BBC Beds, Bucks & Herts | Bedfordshire, Buckinghamshire, Hertfordshire, Luton, Bedford, Milton Keynes, Aylesbury, Watford, Stevenage, Hemel Hempstead, St Albans, High Wycombe, Dunstable |
| **Berkshire** | BBC Berkshire | Berkshire, Slough, Bracknell, Maidenhead, Newbury, Windsor, Wokingham, Thatcham |
| **Birmingham & Black Country** | BBC Birmingham<br>Birmingham Mail | Birmingham, Black Country, Wolverhampton, Dudley, Walsall, West Bromwich, Solihull, Sandwell, Brummie |
| **Bristol** | BBC Bristol<br>Bristol Post | Bristol, Avonmouth, Bedminster, Filton, Clevedon, Portishead |
| **Cambridgeshire** | BBC Cambridgeshire<br>Cambridgeshire Live | Cambridgeshire, Cambridge, Peterborough, Ely, Huntingdon, Wisbech, St Neots |
| **Cornwall** | BBC Cornwall | Cornwall, Cornish, Truro, Falmouth, Penzance, Newquay, St Austell, Bodmin, Camborne, Redruth, Launceston, Liskeard, Saltash, Bude, Wadebridge, Padstow, St Ives, Helston, Hayle, Looe, Fowey, Isles of Scilly |
| **Coventry & Warwickshire** | BBC Coventry & Warwickshire | Coventry, Warwickshire, Warwick, Nuneaton, Leamington Spa, Stratford-upon-Avon, Bedworth, Kenilworth |
| **Cumbria** | BBC Cumbria | Cumbria, Carlisle, Barrow-in-Furness, Kendal, Whitehaven, Workington, Penrith, Windermere, Lake District, Ambleside |
| **Derbyshire** | BBC Derbyshire<br>Derby Telegraph | Derbyshire, Derby, Chesterfield, Buxton, Glossop, Ilkeston, Swadlincote, Peak District, Matlock |
| **Devon** | BBC Devon | Devon, Devonian, Plymouth, Exeter, Torquay, Paignton, Torbay, Barnstaple, Bideford, Ilfracombe, Tiverton, Exmouth, Sidmouth, Dawlish, Teignmouth, Newton Abbot, Totnes, Dartmouth, Kingsbridge, Salcombe, Brixham, Ivybridge, Tavistock, Okehampton, Honiton, Dartmoor, Exmoor |
| **Dorset** | BBC Dorset | Dorset, Bournemouth, Poole, Weymouth, Dorchester, Bridport, Sherborne, Swanage, Blandford Forum |
| **Essex** | BBC Essex<br>Essex Live | Essex, Chelmsford, Colchester, Southend, Basildon, Harlow, Brentwood, Clacton, Braintree, Grays, Romford |
| **Gloucestershire** | BBC Gloucestershire<br>Gloucestershire Live | Gloucestershire, Gloucester, Cheltenham, Stroud, Cirencester, Tewkesbury, Forest of Dean, Cotswolds |
| **Hampshire & Isle of Wight** | BBC Hampshire | Hampshire, Southampton, Portsmouth, Winchester, Basingstoke, Andover, Aldershot, Farnborough, Eastleigh, Fareham, Gosport, Isle of Wight, Ryde, Cowes, Havant |
| **Herefordshire & Worcestershire** | BBC Hereford & Worcester | Herefordshire, Worcestershire, Hereford, Worcester, Kidderminster, Redditch, Malvern, Bromsgrove, Evesham, Ledbury |
| **Humberside** | BBC Humberside<br>Hull Daily Mail | Humberside, Hull, Grimsby, Scunthorpe, Beverley, Bridlington, Goole, Cleethorpes, Humber |
| **Kent** | BBC Kent<br>Kent Live | Kent, Canterbury, Maidstone, Dover, Folkestone, Margate, Ramsgate, Ashford, Gravesend, Tunbridge Wells, Chatham, Sittingbourne, Whitstable, Medway |
| **Lancashire** | BBC Lancashire | Lancashire, Preston, Blackpool, Blackburn, Burnley, Lancaster, Chorley, Accrington, Morecambe, Ormskirk, Fylde |
| **Leeds & West Yorkshire** | BBC Leeds<br>Yorkshire Post | Leeds, West Yorkshire, Bradford, Wakefield, Huddersfield, Dewsbury, Keighley, Batley, Pontefract, Ilkley |
| **Leicester & Rutland** | BBC Leicester<br>Leicester Mercury | Leicester, Leicestershire, Loughborough, Hinckley, Melton Mowbray, Coalville, Market Harborough, Rutland, Oakham |
| **Lincolnshire** | BBC Lincolnshire | Lincolnshire, Lincoln, Grantham, Skegness, Spalding, Louth, Sleaford, Gainsborough |
| **London** | BBC London<br>The Standard | London, Greater London, Londoner, Londoners, Hackney, Islington, Southwark, Lambeth, Tower Hamlets, Wandsworth, Hammersmith, Shoreditch, Whitechapel, Peckham, Brixton, Clapham, Croydon, Ealing, Haringey, Newham, Barnet, Enfield, Hounslow, Hillingdon, Uxbridge, Wembley, Camden Town, Notting Hill, Canary Wharf, Heathrow |
| **Greater Manchester** | BBC Manchester<br>Manchester Evening News | Manchester, Greater Manchester, Mancunian, Salford, Stockport, Oldham, Rochdale, Bolton, Wigan, Tameside, Trafford, Altrincham, Ashton-under-Lyne, Sale |
| **Merseyside** | BBC Merseyside<br>Liverpool Echo | Merseyside, Liverpool, Wirral, Birkenhead, St Helens, Southport, Bootle, Kirkby, Wallasey, Scouse |
| **Norfolk** | BBC Norfolk | Norfolk, Norwich, Great Yarmouth, King's Lynn, Thetford, Dereham, Cromer, Diss, Broads |
| **Northamptonshire** | BBC Northamptonshire | Northamptonshire, Northampton, Kettering, Corby, Wellingborough, Daventry, Rushden |
| **Nottinghamshire** | BBC Nottingham<br>Nottingham Post | Nottingham, Nottinghamshire, Mansfield, Newark, Worksop, Retford, Beeston, Hucknall |
| **Oxfordshire** | BBC Oxford | Oxford, Oxfordshire, Banbury, Bicester, Abingdon, Witney, Didcot, Henley-on-Thames |
| **Shropshire** | BBC Shropshire | Shropshire, Shrewsbury, Telford, Oswestry, Bridgnorth, Ludlow, Market Drayton |
| **Somerset** | BBC Somerset | Somerset, Taunton, Yeovil, Bridgwater, Glastonbury, Frome, Minehead, Burnham-on-Sea, Mendip |
| **South Yorkshire** | BBC South Yorkshire | South Yorkshire, Sheffield, Doncaster, Rotherham, Barnsley |
| **Stoke & Staffordshire** | BBC Stoke & Staffordshire<br>Stoke Sentinel | Staffordshire, Stoke-on-Trent, Stafford, Burton upon Trent, Newcastle-under-Lyme, Lichfield, Tamworth, Cannock, Potteries |
| **Suffolk** | BBC Suffolk | Suffolk, Ipswich, Bury St Edmunds, Lowestoft, Felixstowe, Newmarket, Sudbury, Haverhill |
| **Sussex** | BBC Sussex | Sussex, Brighton, Hove, Eastbourne, Hastings, Worthing, Crawley, Chichester, Bognor Regis, Horsham, Lewes |
| **Teesside** | BBC Tees | Teesside, Middlesbrough, Stockton-on-Tees, Redcar, Hartlepool, Darlington, Billingham, Cleveland |
| **Tyne & Wear** | BBC Tyne & Wear<br>ChronicleLive | Tyne and Wear, Newcastle upon Tyne, Gateshead, Sunderland, South Shields, Whitley Bay, Jarrow, Tynemouth, Geordie, Northumberland |
| **Wiltshire** | BBC Wiltshire | Wiltshire, Swindon, Salisbury, Chippenham, Trowbridge, Devizes, Marlborough, Stonehenge |
| **York & North Yorkshire** | BBC York | York, North Yorkshire, Harrogate, Scarborough, Northallerton, Selby, Whitby, Ripon, Skipton, Malton |
| **Scotland** | BBC Scotland<br>Glasgow Live<br>Edinburgh Live | Scotland, Scottish, Scots, Glasgow, Edinburgh, Aberdeen, Dundee, Inverness, Stirling, Falkirk, Paisley, Fife, Highlands, Hebrides, Orkney, Shetland, Holyrood, Ayrshire, Lanarkshire |
| **Wales** | BBC Wales<br>WalesOnline | Wales, Welsh, Cardiff, Swansea, Wrexham, Aberystwyth, Merthyr Tydfil, Rhondda, Caerphilly, Bridgend, Llandudno, Senedd, Gwynedd, Powys, Pembrokeshire, Snowdonia, Eryri, Neath, Port Talbot |
| **Northern Ireland** | BBC Northern Ireland<br>Belfast Live | Northern Ireland, Belfast, Derry, Londonderry, Lisburn, Newry, Armagh, Ballymena, Coleraine, Enniskillen, Omagh, Stormont, Ulster, Antrim, Fermanagh |

## Not in the UK?

The place matching itself is generic — `LOCAL_PLACES` and `COUNTRY_TERMS` are
just word lists, and `geo.py` does not care which country they name. What is
UK-specific is this table of regional publishers. Set `LOCAL_PLACES` and
`COUNTRY_TERMS` to your own, and add your local feeds to `feeds.yaml` by hand;
everything downstream works the same way.

---
*Generated from `app/intel_brief/uk_regions.py`, which is the source of truth.*
