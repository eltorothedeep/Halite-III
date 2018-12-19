#!/usr/bin/env python3

# Import the Halite SDK, which will let you interact with the game.
import hlt
from hlt import constants

import random
import logging
import copy
from enum import IntEnum, auto

# v1  base bot 
# v2  better movement
# v3  goal based movement
# v4  stuck resolution
# v5  Fib based ship cost, no limit, no delay, return when closer to full
# v6  Homing at end, start with RETURNING, EXPLORE range increase over game after RETURNING, 
#     +1 range on EXPLORING, fix no move bug, strip mine
# v7  Third Fib for ship cost hike, stop shipbuilding when HOMING, HOMING sooner because more ships,
#     Dropoffs, progressively deeper extraction
# v8  Fix log crash, smaller create delay, lower dropoffoverhead
# v9  Dropoff pos range smaller, Pause every other frame when RETURNING, Fix return to storage bug, limit ship creation to first 80%
# v10 Two-third fib, extract more, 
# v11 must move if no halite
# v12 radial explore, don't pause if too little halite, fix pause/unpause, fibbing ratio by map size
# v13 dropoff fixes - check when converting, dropoff count based on map size, dropoff count also based on average distance(?), planned_dropoffs
# v14 here and near ship switch for dropoff, 10% dropoff overhead, one less dropoff for 32x32, closer average distance for conversion
# v15 Metrics (shipfib, max_dropoff) based on map size + player count, return with smaller loads as game progresses, kamikaze enemy ships on dropoffs, 
#     fix for ships stalled on dropoff
# v16 Better nav to avoid self collisions, better radial org, don't kamikaze self on dropoffs
# v17 Fewer ships, better dropoff selection, don't try to move if you don't have enough halite, no SideStep in HOMING
# v18 Ditch naive_navigate... Hooray!! Stop making ships based on map size/numplayers

class shipInfo(IntEnum):
    STATE = 0
    GOAL = auto()
    LASTPOS = auto()
    PAUSE = auto()
    DROPID = auto()
    TURNTAKEN = auto()
#

class shipState(IntEnum):
    RETURNING = auto()
    EXPLORING = auto()
    CONVERTING = auto()
    HOMING = auto()
#

class dropInfo(IntEnum):
    RADIAL = 0
    SHIP_HERE = auto()
    SHIP_NEAR = auto()
#

log_nav = False
log_pause = False
log_dropoffs = False
log_here_near = False
verbose = False

useSaboteurs = False
homing_begun = False
reservedfordropoff = 0
dropoffcostoverhead = 1.1
createshipturn = 0
radial = [[0, 1],[1, 0],[0, -1],[-1, 0],[1, 1],[1, -1],[-1, -1],[-1, 1]]
ship_status = {}
dropoff_status = {}
planned_dropoffs = {}
nav_plan = {}
sizeratio2 = {
    32:{2:[0.70,2,0.7], 4:[0.75,1,0.6]}, 
    40:{2:[0.60,3,0.8], 4:[0.67,1,0.8]}, 
    48:{2:[0.40,4,0.8], 4:[0.50,2,0.8]}, 
    56:{2:[0.33,4,0.8], 4:[0.45,2,0.8]}, 
    64:{2:[0.25,5,0.8], 4:[0.40,3,0.8]}
}

testmove_dir_list = {
    hlt.Direction.North: [hlt.Direction.North, hlt.Direction.East, hlt.Direction.West, hlt.Direction.South],
    hlt.Direction.South: [hlt.Direction.South, hlt.Direction.West, hlt.Direction.East, hlt.Direction.North],
    hlt.Direction.East : [hlt.Direction.East, hlt.Direction.South, hlt.Direction.North, hlt.Direction.West],
    hlt.Direction.West : [hlt.Direction.West, hlt.Direction.North, hlt.Direction.South, hlt.Direction.East]    
}

max_dropoffs = 1
average_halite_ratio = 0

def fibbing(n):
    if n == 0:
        return 0
    elif n==1 or n==2:
        return 1
        
    return fibbing(n-1)+fibbing(n-2)
#

def GetFib(fn, arg):
    fibs = {}
    if arg not in fibs:
        fibs[arg] = fn(arg)
    return fibs[arg]
#

def GetShipBuildThreshold(num):
    return int(constants.SHIP_COST * (1+(GetFib(fibbing, num)/100))) + reservedfordropoff
#    

def IsAtEdgeOfMap(position, map):
    if (position.x == 0 or position.x == map.width-1 or position.y == 0 or position.y == map.height-1):
        return True
    #
    return False
#

def PositionToNavIndex(position, the_map):
    norm = the_map.normalize(position)
    return (norm.x * the_map.width + norm.y)
#

def GetHaliteRichness(curPos, range, the_map):
    max_halite = 0
    cur_halite = 0
    range *= 4
    adList = curPos.get_surrounding_cardinals()
    for adjacent in adList:
        if range > 0:
            for ad1 in adjacent.get_surrounding_cardinals():
                adList.append(the_map.normalize(ad1))
            #
            range -= 1
        #
        cur_halite += the_map[adjacent].halite_amount
        max_halite += constants.MAX_HALITE
    #
    #if log_dropoffs:
    #    logging.info("Test {} CurHalite {} MaxHalite {} Ratio {}".format(curPos, cur_halite, max_halite, cur_halite/max_halite))
    #
    return cur_halite / max_halite
#

def GetRichestPosition(curPos, range, mustmove, avoidedges, map):
    global nav_plan
    
    first = mustmove
    range *= 4
    best = curPos
    max = map[curPos].halite_amount
    adList = curPos.get_surrounding_cardinals()
    for adjacent in adList:
        if range > 0:
            for ad1 in adjacent.get_surrounding_cardinals():
                adList.append(map.normalize(ad1))
            #
            range -= 1
        #
        nav_idx = PositionToNavIndex(adjacent, map)
        if map[adjacent].is_empty and not nav_idx in nav_plan and \
            ((first and map[adjacent].halite_amount >= max) or (map[adjacent].halite_amount > max)):
            
            adjacent = map.normalize(adjacent)
            if avoidedges:
                if not IsAtEdgeOfMap(ad1, map):
                    best = adjacent
                    max = map[adjacent].halite_amount
                    first = False
                #
            else:
                best = adjacent
                max = map[adjacent].halite_amount
                first = False
            #
        #
    #
    return best
#

def ConvertToDropoff(ship, me, av_storage_dist, map):
    global planned_dropoffs
    global max_dropoffs
    global average_halite_ratio
    
    #if log_dropoffs:
    #    d_count = len(me.get_dropoffs()) + len(planned_dropoffs)
    #    logging.info("Num Ships {} Avg Dist {} Halite {} Dropoffs {} AV Halite {}".format(numships, av_storage_dist, me.halite_amount, d_count, average_halite_ratio))
    #    
    
    min_distance = map.height / 4
    if ((av_storage_dist > min_distance) or len(me.get_ships()) > (1+len(me.get_dropoffs()))*10) and \
        (me.halite_amount > int(constants.DROPOFF_COST * dropoffcostoverhead)) and \
        (len(me.get_dropoffs()) + len(planned_dropoffs) < max_dropoffs) and \
        (GetHaliteRichness(ship.position, 3, map) >= average_halite_ratio):
        
        distance = map.calculate_distance(ship.position, me.shipyard.position)
        #if log_dropoffs:
        #    logging.info("Base conditions met - checking for distance")
        #    logging.info("Shipyard Distance {}".format(distance))
        #    
        far_enough = distance >= min_distance
        for dropoff in me.get_dropoffs():
            distance = map.calculate_distance(ship.position, dropoff.position)
            #if log_dropoffs:
            #    logging.info("Dropoff {} Distance {}".format(dropoff.id, distance))
            #    
            far_enough = far_enough and (distance >= min_distance)
        #
        for dropoff, position in planned_dropoffs.items():
            distance = map.calculate_distance(ship.position, position)
            #if log_dropoffs:
            #    logging.info("Planned Dropoff {} Distance {}".format(dropoff, distance))
            #    
            far_enough = far_enough and (distance >= min_distance)
        #
        if far_enough:
            dropoffpos = GetRichestPosition(ship.position, 2, False, True, map)
            planned_dropoffs[ship.id] = dropoffpos
            #if log_dropoffs:
            #    logging.info("Dropoff Approved")
            #    
            return True, dropoffpos
        #
    #
    #if log_dropoffs:
    #    logging.info("Dropoff Denied")
    #
    return False, None    
#

def GetClosestStoragePosition(position, me, map):
    storageindex = 0
    min = map.calculate_distance(ship.position, me.shipyard.position)
    pos = me.shipyard.position
    for dropoff in me.get_dropoffs():
        dist = map.calculate_distance(ship.position, dropoff.position)
        if (dist <= min):
            min = dist
            pos = dropoff.position
            storageindex = dropoff.id
        #
    #
    return pos,storageindex
#

def IsOnAnyDropoff(the_ship, me):
    if the_ship.position == me.shipyard.position:
        return 0
    #
    for dropoff in me.get_dropoffs():
        if the_ship.position == dropoff.position:
            return dropoff.id
        #
    #
    return -1
#

def GetRadialExplorePos(pos, dropid, max_radial):
    global radial
    global dropoff_status
    if dropid not in dropoff_status:
        dropoff_status[dropid] = [0, None, None]
    #
    mul = 1+int(dropoff_status[dropid][dropInfo.RADIAL]/8)
    idx = dropoff_status[dropid][dropInfo.RADIAL] % 8
    offset = radial[idx]
    radpos = copy.deepcopy(pos)
    radpos.x += offset[0]*mul
    radpos.y += offset[1]*mul
    dropoff_status[dropid][dropInfo.RADIAL] += 1
    #if dropoff_status[dropid][dropInfo.RADIAL] > max_radial:
    #    dropoff_status[dropid][dropInfo.RADIAL] = 1
    #
    return radpos
#

def BlockNavIndex(the_ship, position, the_map):
    global nav_plan
    global log_pause

    nav_idx = PositionToNavIndex(position, the_map)
    #if log_pause:
    #    plan = -1
    #    if nav_idx in nav_plan:
    #        plan = nav_plan[nav_idx]
    #    #
    #    logging.info("NAV_IDX {} NAV_PLAN {}".format(nav_idx, plan))
    #
    nav_plan[nav_idx] = the_ship.id
    #if log_pause:
    #    logging.info("Mark NAV_PLAN".format(nav_idx, nav_plan[nav_idx]))
    #

#

def UpdateNavPlan(the_ship, the_map, position):
    global ship_status
    global nav_plan
    global log_pause

    # update the nav_map
    nav_idx = PositionToNavIndex(the_ship.position, the_map)
    #if log_pause:
    #    if nav_idx in nav_plan:
    #        plan = nav_plan[nav_idx]
    #    #
    #    logging.info("NAV_IDX {} NAV_PLAN {}".format(nav_idx, plan))
    #
    if nav_idx in nav_plan and nav_plan[nav_idx] == the_ship.id:
        del nav_plan[nav_idx]
        #if log_pause:
        #    logging.info("Clear NAV_PLAN")
        #
    #
    BlockNavIndex(the_ship, position, the_map)
	
	# mark the map
    the_map[position].mark_unsafe(the_ship)
	
	# mark the ship status
    ship_status[the_ship.id][shipInfo.PAUSE] = (the_ship.position == position)
    ship_status[the_ship.id][shipInfo.TURNTAKEN] = True
#

def PauseShip(the_ship, the_map, command_buffer):
    global log_nav
    global log_pause
	
    #if log_nav:
    #    logging.info("Ship {} Pausing".format(the_ship.id))
    #
    
    # Update Nav Details
    UpdateNavPlan(the_ship, the_map, the_ship.position)

	# add the command
    command_buffer.append(the_ship.stay_still())
#

def TestMove(the_ship, the_map, the_move, me):
    global nav_plan
    #global log_pause
    
    new_pos = the_ship.position.directional_offset(the_move)
    nav_idx = PositionToNavIndex(new_pos, the_map)
    #if log_pause:
    #    plan = -1
    #    if nav_idx in nav_plan:
    #        plan = nav_plan[nav_idx]
    #    #
    #    logging.info("NAV_IDX {} NAV_PLAN {}".format(nav_idx, plan))
    #
    
    if nav_idx not in nav_plan:
        return (the_map[new_pos].is_empty or the_map[new_pos].has_structure or \
            (the_map[new_pos].is_occupied and the_map[new_pos].ship.owner != me.id))
    #
    return False
#

def GetNextMove(the_ship, the_map, me):
    global ship_status
    global testmove_dir_list
    
    #return the_map.naive_navigate(the_ship, ship_status[the_ship.id][shipInfo.GOAL])
    
    # Already there - PAUSE
    if ship_status[the_ship.id][shipInfo.GOAL] == the_ship.position:
        #logging.info("HUH!! Ship {} State {} Cur {}".format(the_ship.id, ship_status[the_ship.id][shipInfo.STATE], the_ship.position))
        return hlt.Direction.Still
    #
    
    # Find the Delta
    delta_pos = ship_status[the_ship.id][shipInfo.GOAL] - the_ship.position
    
    # Fix X for Torus
    if abs(delta_pos.x) > the_map.width / 2:
        if delta_pos.x < 0:
            delta_pos.x = 1 + the_map.width - abs(delta_pos.x)
        else:
            delta_pos.x = abs(delta_pos.x) - the_map.width - 1
        #
    #
    
    # Fix Y for Torus
    if abs(delta_pos.y) > the_map.height / 2:
        if delta_pos.y < 0:
            delta_pos.y = 1 + the_map.height - abs(delta_pos.y)
        else:
            delta_pos.y = abs(delta_pos.y) - the_map.height - 1
        #
    #
    #logging.info("Delta {}".format(delta_pos))
        
    # Pick a starting direction
    if abs(delta_pos.x) > abs(delta_pos.y):
        move = (int(delta_pos.x/abs(delta_pos.x)),0)
    elif abs(delta_pos.x) < abs(delta_pos.y):
        move = (0, int(delta_pos.y/abs(delta_pos.y)))
    else:
        if random.randint(0,100) > 50:
            move = (int(delta_pos.x/abs(delta_pos.x)),0)
        else:
            move = (0, int(delta_pos.y/abs(delta_pos.y)))
        #
    #
    
    # Spin the wheel and find a move!!
    for cardinal in testmove_dir_list[move]:
        if TestMove(the_ship, the_map, cardinal, me):
            return cardinal
        #
    #
    
    # Failed to find a move - Pause
    return hlt.Direction.Still
#

def NavigateShip(the_ship, the_map, me, command_buffer):
    global ship_status
    global verbose
	
    #if verbose:
    #    logging.info("Ship {} Navigating".format(the_ship.id))
	#
	
    move = GetNextMove(the_ship, the_map, me)
    #if verbose:
    #    logging.info("Ship {} Current {} Goal {} Move {}".format(the_ship.id, the_ship.position, ship_status[the_ship.id][shipInfo.GOAL], move))
    #
    
    # compute new postion
    newpos = the_ship.position.directional_offset(move)
    #if True:
    #    logging.info("Cr {} Gl {} Mv {} Nx {}".format(the_ship.position, ship_status[the_ship.id][shipInfo.GOAL], move, newpos))
    #
    
    if newpos == the_ship.position:
        # Pause if nowhere to go
        PauseShip(the_ship, the_map, command_buffer)
        return False, 0
    else:   
        UpdateNavPlan(the_ship, the_map, newpos)
    	
        # add the command
        command_buffer.append(the_ship.move(move))
        
        #if log_nav:
        #    logging.info("Ship {} Move {}".format(the_ship.id, move))
        #
    #
    
    # return cost to move
    return True, int(the_map[the_ship.position].halite_amount * 0.1)
#


# This game object contains the initial game state 
game = hlt.Game()
# Respond with your name.
game.ready("DeepCv18")

shipfibratio = sizeratio2[game.game_map.height][len(game.players)][0]
max_dropoffs = sizeratio2[game.game_map.height][len(game.players)][1]
end_ship_create = sizeratio2[game.game_map.height][len(game.players)][2]
map_width = game.game_map.width
map_height = game.game_map.height

num_samples = 0
log_dropoffs = False
for r in range(0, map_width, int(map_width/8)):
    for c in range(0, map_height, int(map_height/8)):
        cur_pos = hlt.Position(r,c)
        average_halite_ratio += GetHaliteRichness(cur_pos, 4, game.game_map)
        num_samples += 1
    #
#
#logging.info("sum {} num {} avg {}".format(average_halite_ratio, num_samples, average_halite_ratio/num_samples))
average_halite_ratio /= num_samples
log_dropoffs = False

while True:
    # Get the latest game state.
    game.update_frame()
    # You extract player metadata and the updated map metadata here for convenience.
    me = game.me
    game_map = game.game_map
    
    # clear the nav_plan for the turn
    nav_plan.clear()

    # setup parameters for this turn    
    extractionratio = 25 + (int(game.turn_number/100) * 5)
    min_halite = constants.MAX_HALITE / extractionratio
    return_threshold = int(constants.MAX_HALITE * 0.5) #int(constants.MAX_HALITE * (0.5-(0.25*game.turn_number/constants.MAX_TURNS)))
    
    # A command queue holds all the commands you will run this turn.
    command_queue = []
    
    numships = len(me.get_ships())
    numdropoffs = len(me.get_dropoffs())
    exploring = 0
    returning = 0
    av_storage_dist = 0
    #if verbose:
    #    logging.info("PRE-PASS")
    #
    for ship in me.get_ships():
        BlockNavIndex(ship, ship.position, game_map)
        if ship.id not in ship_status:
            ship_status[ship.id] = [shipState.RETURNING, ship.position, ship.position, False, 0, False]
            #if verbose:
            #    logging.info("New Ship {}".format(ship.id))
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.RETURNING:
            #if verbose:
            #    logging.info("Ship {} RETURNING".format(ship.id))
            #
            if ship.position == ship_status[ship.id][shipInfo.LASTPOS] and not ship_status[ship.id][shipInfo.PAUSE]: 
                ship_status[ship.id][shipInfo.GOAL] = GetRichestPosition(ship.position, 0, True, False, game_map)
                #if verbose:
                #    logging.info("Ship {} didn't move last frame".format(ship.id))
                #
            #
            ship_status[ship.id][shipInfo.PAUSE] = not ship_status[ship.id][shipInfo.PAUSE]
            returning += 1
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.EXPLORING:
            #if verbose:
            #    logging.info("Ship {} EXPLORING".format(ship.id))
            #
            dropoffid = IsOnAnyDropoff(ship, me)
            if dropoffid >= 0:
                ship_status[ship.id][shipInfo.STATE] = shipState.RETURNING
                ship_status[ship.id][shipInfo.GOAL] = ship.position
                ship_status[ship.id][shipInfo.DROPID] = dropoffid
                ship_status[ship.id][shipInfo.LASTPOS] = ship.position
                returning += 1
            else:
                exploring += 1
                #if ship.position == ship_status[ship.id][shipInfo.LASTPOS] and not ship_status[ship.id][shipInfo.PAUSE]:
                    #ship_status[ship.id][shipInfo.GOAL] = GetRichestPosition(ship.position, 0, True, False, game_map)
                    #if verbose:
                    #    logging.info("Ship {} didn't move last frame".format(ship.id))
                    #
                #
                closest, index = GetClosestStoragePosition(ship.position, me, game_map)
                av_storage_dist += game_map.calculate_distance(ship.position, closest)
            #
            ship_status[ship.id][shipInfo.PAUSE] = False
        #
        if ship_status[ship.id][shipInfo.STATE] != shipState.HOMING:
            turns_left = constants.MAX_TURNS - game.turn_number
            if turns_left < 100:
                storage, id = GetClosestStoragePosition(ship.position, me, game_map)
                distance = game_map.calculate_distance(ship.position, storage)
                if (distance * 2.5) > turns_left:
                    if ship_status[ship.id][shipInfo.STATE] == shipState.EXPLORING:
                        exploring -= 1
                    elif ship_status[ship.id][shipInfo.STATE] == shipState.RETURNING:
                        returning -= 1
                    elif ship_status[ship.id][shipInfo.STATE] == shipState.CONVERTING:
                        reservedfordropoff -= int(constants.DROPOFF_COST * dropoffcostoverhead)
                    #
                    ship_status[ship.id][shipInfo.STATE] = shipState.HOMING
                    ship_status[ship.id][shipInfo.GOAL] = storage
                    homing_begun = True
                    #logging.info("Ship {} HOMING Distance {}".format(ship.id, distance))
                #
            #
        #
        #logging.info("Ship {} state {} goal {} pos {} halite {}.".format(ship.id, str(ship_status[ship.id][shipInfo.STATE]), ship_status[ship.id][shipInfo.GOAL], ship.position, ship.halite_amount))
        ship_status[ship.id][shipInfo.LASTPOS] = ship.position
        ship_status[ship.id][shipInfo.TURNTAKEN] = False
    #
    #logging.info("PrePro Complete")
    if exploring > 0:
        av_storage_dist /= exploring
    #
    dropoffthisturn = False
    costthisturn = 0
    
    ship_near_shipyard = False
    
    #if verbose:
    #    logging.info("MAIN-PASS")
    #
    for ship in me.get_ships():
        if ship.halite_amount < int(game_map[ship.position].halite_amount * 0.15):
            PauseShip(ship, game_map, command_queue)
        elif ship_status[ship.id][shipInfo.STATE] == shipState.RETURNING:
            #if verbose:
            #    logging.info("Ship {} RETURNING".format(ship.id))
            #
            if ship.position == ship_status[ship.id][shipInfo.GOAL]:
                ship_status[ship.id][shipInfo.STATE] = shipState.EXPLORING
                #ship_status[ship.id][shipInfo.GOAL] = GetRichestPosition(ship.position, 1, True, False, game_map)
                ship_status[ship.id][shipInfo.GOAL] = game_map.normalize(GetRadialExplorePos(ship.position, ship_status[ship.id][shipInfo.DROPID], game_map.width/4))
                if ship_status[ship.id][shipInfo.DROPID] not in dropoff_status:
                    dropoff_status[ship_status[ship.id][shipInfo.DROPID]] = [0, None, None]
                #
                dropoff_status[ship_status[ship.id][shipInfo.DROPID]][dropInfo.SHIP_HERE] = ship.id
                #if log_here_near:
                #    logging.info("Ship {} HERE at Dropoff {}".format(ship.id, ship_status[ship.id][shipInfo.DROPID]))
                #
            elif game_map.calculate_distance(ship.position, ship_status[ship.id][shipInfo.GOAL]) == 1:
                if ship_status[ship.id][shipInfo.DROPID] not in dropoff_status:
                    dropoff_status[ship_status[ship.id][shipInfo.DROPID]] = [0, None, None]
                #
                if dropoff_status[ship_status[ship.id][shipInfo.DROPID]][dropInfo.SHIP_NEAR] == None:
                    dropoff_status[ship_status[ship.id][shipInfo.DROPID]][dropInfo.SHIP_NEAR] = ship.id
                    if ship_status[ship.id][shipInfo.DROPID] == 0:
                        ship_near_shipyard = True
                    #
                    #if log_here_near:
                    #    logging.info("Ship {} first to arrive NEAR Dropoff {} ".format(ship.id, ship_status[ship.id][shipInfo.DROPID]))
                    #
                else:
                    PauseShip(ship, game_map, command_queue)
                    #if log_here_near:
                    #    logging.info("Ship {} NEAR Dropoff {}. PAUSED".format(ship.id, ship_status[ship.id][shipInfo.DROPID]))
                    #
                #            
            else:
                if not (game_map[ship.position].halite_amount < int(min_halite/2) or not ship_status[ship.id][shipInfo.PAUSE]):                   
                    #if verbose:
                    #    logging.info("Ship {} pausing".format(ship.id))
                    #
                    PauseShip(ship, game_map, command_queue)
                #
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.HOMING:
            #if verbose:
            #    logging.info("Ship {} HOMING - crash in".format(ship.id))
            #
            if game_map.calculate_distance(ship.position, ship_status[ship.id][shipInfo.GOAL]) == 1:
                #if verbose:
                #    logging.info("Ship {} slam home".format(ship.id))
                #
                moves = game_map.get_unsafe_moves(ship.position, ship_status[ship.id][shipInfo.GOAL])
                command_queue.append(ship.move(moves[0]))
                ship_status[ship.id][shipInfo.TURNTAKEN] = True
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.CONVERTING:
            #if verbose:
            #    logging.info("Ship {} CONVERTING".format(ship.id))
            #
            if ship.position == ship_status[ship.id][shipInfo.GOAL]:
                if game_map[ship.position].structure is not None:
                    ship_status[ship.id][shipInfo.STATE] = shipState.RETURNING
                    ship_status[ship.id][shipInfo.GOAL], ship_status[ship.id][shipInfo.DROPID] = GetClosestStoragePosition(ship.position, me, game_map)                
                elif me.halite_amount - costthisturn > int(constants.DROPOFF_COST * dropoffcostoverhead):
                    command_queue.append(ship.make_dropoff())
                    del planned_dropoffs[ship.id]
                    reservedfordropoff -= int(constants.DROPOFF_COST * dropoffcostoverhead)
                    costthisturn += constants.DROPOFF_COST
                    ship_status[ship.id][shipInfo.TURNTAKEN] = True
                    #if verbose:
                    #    logging.info("Ship {} CONVERTING - convert".format(ship.id))
                    #
                else:
                    #if verbose:
                    #    logging.info("Ship {} CONVERTING - wait".format(ship.id))
                    #
                    PauseShip(ship, game_map, command_queue)
                #
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.EXPLORING:
            #if verbose:
            #    logging.info("Ship {} EXPLORING".format(ship.id))
            #
            if ship.is_full or (ship.halite_amount > return_threshold and returning < numdropoffs+2):
                #if verbose:
                #    logging.info("Ship {} EXPLORING - full[enough]".format(ship.id))
                #
                convert = False
                dropoffpos = ship.position
                if not dropoffthisturn:
                    convert, dropoffpos = ConvertToDropoff(ship, me, av_storage_dist, game_map)
                #
                if convert:
                    ship_status[ship.id][shipInfo.STATE] = shipState.CONVERTING
                    ship_status[ship.id][shipInfo.GOAL] = dropoffpos
                    reservedfordropoff += int(constants.DROPOFF_COST * dropoffcostoverhead)
                    dropoffthisturn = True
                    #if verbose:
                    #    logging.info("Ship {} EXPLORING - switching to CONVERTING".format(ship.id))
                    #
                else:
                    ship_status[ship.id][shipInfo.STATE] = shipState.RETURNING
                    ship_status[ship.id][shipInfo.GOAL], ship_status[ship.id][shipInfo.DROPID] = GetClosestStoragePosition(ship.position, me, game_map)
                    #if verbose:
                    #    logging.info("Ship {} EXPLORING - switching to RETURNING".format(ship.id))
                    #
                #
            elif ship_status[ship.id][shipInfo.GOAL] is not None:
                if ship_status[ship.id][shipInfo.GOAL] == ship.position:
                    ship_status[ship.id][shipInfo.GOAL] = None
                    PauseShip(ship, game_map, command_queue)
                    #if verbose:
                    #    logging.info("Ship {} EXPLORING - reached goal, pausing".format(ship.id))
                    #
                #
            elif game_map[ship.position].halite_amount < min_halite:
                ship_status[ship.id][shipInfo.GOAL] = GetRichestPosition(ship.position, 1, game_map[ship.position].halite_amount==0, False, game_map)
                #if verbose:
                #    logging.info("Ship {} EXPLORING - emptied cell goal, moving to new goal".format(ship.id))
                #
            else:
                PauseShip(ship, game_map, command_queue)
                #if verbose:
                #    logging.info("Ship {} EXPLORING - pausing".format(ship.id))
                #
            #
        #
    #
    
    for id, info in dropoff_status.items():
        ship_here_id = info[dropInfo.SHIP_HERE]
        ship_near_id = info[dropInfo.SHIP_NEAR]
        #if log_here_near:
        #    logging.info("Dropoff {} Ship Here {} Near {} ".format(id, ship_here_id, ship_near_id))
        #
        if ship_here_id and ship_near_id:
            ship_here = me.get_ship(ship_here_id)
            ship_near = me.get_ship(ship_near_id)
            to_near = game_map.get_unsafe_moves(ship_here.position, ship_near.position)
            UpdateNavPlan(ship_here, game_map, ship_near.position)
            command_queue.append(ship_here.move(to_near[0]))
            to_here = game_map.get_unsafe_moves(ship_near.position, ship_here.position)
            UpdateNavPlan(ship_near, game_map, ship_here.position)
            command_queue.append(ship_near.move(to_here[0]))
            #if log_here_near:
            #    logging.info("Ship Here & Near switched")
            #
        elif ship_here_id:
            ship_here = me.get_ship(ship_here_id)
            success, cost = NavigateShip(ship_here, game_map, me, command_queue)
            costthisturn += cost
            if not success:
                #if log_here_near:
                #    logging.info("Ship Here Stalled")
                #
                ship_status[ship_here_id][shipInfo.STATE] = shipState.RETURNING
                ship_status[ship_here_id][shipInfo.GOAL] = ship_here.position
                ship_status[ship_here_id][shipInfo.PAUSE] = False
                ship_status[ship_here_id][shipInfo.TURNTAKEN] = True
            #
        elif ship_near_id:
            ship_near = me.get_ship(ship_near_id)
            dropoff_cell = game_map[ship_status[ship_near_id][shipInfo.GOAL]]
            if dropoff_cell.is_occupied and dropoff_cell.ship.owner != me.id:
                # kamikaze dropoff squatter
                #if log_here_near:
                #    logging.info("KAMIKAZE: Ship {} Position {} Goal {} Owner {} Me {}".format(ship_near_id, ship_near.position, ship_status[ship_near_id][shipInfo.GOAL], dropoff_cell.ship.owner, me))
                #
                to_dropoff = game_map.get_unsafe_moves(ship_near.position, ship_status[ship_near_id][shipInfo.GOAL])
                UpdateNavPlan(ship_near, game_map, ship_status[ship_near_id][shipInfo.GOAL])
                command_queue.append(ship_near.move(to_dropoff[0]))
            else:
                # move in carefully
                success, cost = NavigateShip(ship_near, game_map, me, command_queue)
                costthisturn += cost
                #if log_here_near:
                #    logging.info("Ship Near Moved")
                #
            #
        #
        info[dropInfo.SHIP_HERE] = None
        info[dropInfo.SHIP_NEAR] = None
    #
    
    #if verbose:
    #    logging.info("POST-PASS")
    #
    for ship in me.get_ships():        
        if not ship_status[ship.id][shipInfo.TURNTAKEN]:
            #if log_nav:
            #    logging.info("Ship {} moving".format(ship.id))
            #
            success, cost = NavigateShip(ship, game_map, me, command_queue)
            costthisturn += cost
        #
    #
       
    # If you're on the first turn and have enough halite, spawn a ship.
    # Don't spawn a ship if you currently have a ship at port, though.
    if game.turn_number <= 1 or \
        ((me.halite_amount >= GetShipBuildThreshold(int(shipfibratio*numships))) and \
        ((game.turn_number - createshipturn) > 2) and \
        game.turn_number < int(constants.MAX_TURNS*end_ship_create) and not \
        homing_begun and not \
        game_map[me.shipyard].is_occupied) and not ship_near_shipyard:
        command_queue.append(game.me.shipyard.spawn())
        createshipturn = game.turn_number
        #if verbose:
        #    logging.info("Create Ship")
        #
    #
    
    # Send your moves back to the game environment, ending this turn.
    game.end_turn(command_queue)
    
    #if verbose and game.turn_number == 100:
    #    break
    #
#