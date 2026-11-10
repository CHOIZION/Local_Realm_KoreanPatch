// Presentation-only adapter. Database names, keys and values are never changed.
globalThis.__localRealmKoreanQA = (() => {
  const types = new Set(['Character','Location','Settlement','Faction','Event','Species','Item']);
  const plurals = new Set(['Characters','Locations','Settlements','Factions','Events','Species','Items']);
  const biomes = new Set(['Deep ocean','Shallow sea','Beach','Grassland','Forest','Jungle','Desert','Swamp','Tundra','Mountain','Snow and ice','Lake']);
  const keys = new Set(['species','age','height','occupation','languages','home','affiliations','status','region','part_of','ruled_by','population','leader','motto','seat_of_power','date','participants','location','lifespan','homeland','material','owner','origin']);
  function display(object, field) {
    if (object == null) return undefined;
    const raw = object[field];
    if (typeof raw !== 'string' || k.language !== 'ko') return raw;
    // Only schema metadata and biome labels: no entity names, prose, tags or notes.
    if (typeof object.plural === 'string' && 'parent_id' in object && types.has(object.name)) {
      if (['name','plural','description'].includes(field)) return _(raw);
    }
    if (typeof object.data_type === 'string' && typeof object.key === 'string' && keys.has(object.key)) {
      if (field === 'label' || field === 'hint') return _(raw);
      if (field === 'key') return _(raw + '|field key');
    }
    if (field === 'name' && Number.isInteger(object.id) && typeof object.color === 'string' && biomes.has(raw)) return _(raw);
    if (field === 'title' && (object.kind === 'type' || String(object.key || '').startsWith('type:')) && (types.has(raw) || plurals.has(raw))) return _(raw);
    if ((field === 'type_name' || field === 'type_plural') && (types.has(raw) || plurals.has(raw))) return _(raw);
    return raw;
  }
  function typeEdit(original, delta) {
    const result = {...delta};
    for (const field of ['name','plural','description']) {
      // A blur without an actual edit must not save the translated display value.
      if (field in result && result[field] === display(original, field)) delete result[field];
    }
    return result;
  }
  return {display, typeEdit};
})();
